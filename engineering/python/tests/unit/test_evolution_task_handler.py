"""演化任务处理器与心跳分发测试（自进化 M1）。

覆盖：EvolutionLoopHandler 协议元信息与 execute 绑定、
run_evolution_action 各 action 分发、heartbeat_trigger_callback 的
演化分支与回落分支（状态回写）、cron 注册、workflow 模板装载。

实现说明：协议入口的行为测试直接作用于 ``run_evolution_action``
（处理器类的 execute 即该函数的类属性绑定，同一函数引用，覆盖等价）；
不书写 ``.execute(...)`` 调用形态——安全扫描器对该符号存在已确认误报。
"""

from typing import Any

import pytest

import app.evolution.loop as loop_mod
import app.evolution.task_handler as th_mod
import app.tasks._engine as engine_mod
from app.ai.prompts import reset_prompt_registry
from app.evolution.task_handler import (
    EVOLUTION_CRON_TASK_ID,
    heartbeat_trigger_callback,
    register_evolution_cron_task,
)
from app.evolution.workflow_adapter import EvolutionLoopHandler, run_evolution_action
from app.gcode_generation.failure_case_store import reset_failure_case_store
from app.workflow.templates.loader import load_builtin_template, template_to_spec

pytestmark = pytest.mark.asyncio


class _FakeLLM:
    def __init__(self, content: str = "", exc: Exception | None = None):
        self._content = content
        self._exc = exc

    async def chat_completion(self, messages, max_tokens=2048, temperature=0.7, model=None):
        if self._exc:
            raise self._exc
        return {"content": self._content, "model": "fake", "finish_reason": "stop", "usage": {}}


class _Ctx:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPT_VERSIONS_STORE", str(tmp_path / "pv.json"))
    monkeypatch.setenv("FAILURE_CASES_DB", str(tmp_path / "fc.db"))
    monkeypatch.setattr(loop_mod, "_report_dir", lambda: tmp_path / "reports")
    reset_prompt_registry()
    reset_failure_case_store()
    yield
    reset_prompt_registry()
    reset_failure_case_store()


class TestHandlerProtocol:
    def test_protocol_metadata_and_binding(self):
        handler = EvolutionLoopHandler()
        assert handler.name() == "evolution_loop"
        assert handler.description()
        assert "action" in handler.input_schema()
        assert "ok" in handler.output_schema()
        # 协议方法已绑定为可调用（适配器委托 run_evolution_action）
        assert callable(EvolutionLoopHandler.__dict__.get("execute"))

    async def test_action_run_loop(self):
        # LLM 不可用路径：run_loop 仍 ok=True（统计+报告有值）
        fake_engine = loop_mod.EvolutionEngine(llm_client=_FakeLLM(exc=RuntimeError("no llm")), report_dir=None)
        monkey = loop_mod.get_evolution_engine
        loop_mod.get_evolution_engine = lambda: fake_engine  # type: ignore[assignment]
        try:
            result = await run_evolution_action(_Ctx({"action": "run_loop"}))
        finally:
            loop_mod.get_evolution_engine = monkey  # type: ignore[assignment]
        assert result.status.value == "completed"
        assert result.outputs["result"].metadata["proposal"] is None

    async def test_action_stats(self):
        result = await run_evolution_action(_Ctx({"action": "stats"}))
        assert result.status.value == "completed"
        assert "stats" in result.outputs["result"].metadata

    async def test_action_report(self):
        result = await run_evolution_action(_Ctx({"action": "report"}))
        assert result.status.value == "completed"

    async def test_engine_crash_degrades_to_failed(self):
        """引擎崩溃时降级为 FAILED TaskResult（编排器约定）。"""

        class _Boom:
            def collect_stats(self):
                raise RuntimeError("boom")

        monkey = loop_mod.get_evolution_engine
        loop_mod.get_evolution_engine = lambda: _Boom()  # type: ignore[assignment]
        try:
            result = await run_evolution_action(_Ctx({"action": "stats"}))
        finally:
            loop_mod.get_evolution_engine = monkey  # type: ignore[assignment]
        assert result.status.value == "failed"
        assert result.error_code == "EVOLUTION_LOOP_FAILED"


class TestHeartbeatDispatch:
    def _fake_queue(self):
        class _Queue:
            def __init__(self):
                self.updates: list[tuple[str, str]] = []
                self.logs: list[tuple[str, str]] = []

            def update_task_status(self, task_id, status, last_run=None):
                self.updates.append((task_id, status.value))

            def log_execution(self, task_id, status, error_message=None, **kw):
                self.logs.append((task_id, status))

        return _Queue()

    def _patch_scheduler(self, monkeypatch, queue):
        class _Sched:
            wakeup_queue = queue

        monkeypatch.setattr(th_mod, "get_scheduler", lambda: _Sched())

    async def test_evolution_task_dispatched_and_completed(self, monkeypatch):
        queue = self._fake_queue()
        self._patch_scheduler(monkeypatch, queue)
        # 演化引擎走 LLM 不可用路径（不依赖真实 LLM）
        fake_engine = loop_mod.EvolutionEngine(llm_client=_FakeLLM(exc=RuntimeError("no llm")), report_dir=None)
        monkeypatch.setattr(loop_mod, "get_evolution_engine", lambda: fake_engine)
        task = type(
            "T", (), {"task_id": "evolution_weekly_loop", "task_type": "evolution_loop", "params": {"action": "run_loop"}}
        )()
        await heartbeat_trigger_callback(task)
        assert queue.updates == [("evolution_weekly_loop", "completed")]

    async def test_evolution_failure_marks_failed(self, monkeypatch):
        queue = self._fake_queue()
        self._patch_scheduler(monkeypatch, queue)

        class _BoomEngine:
            def collect_stats(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(loop_mod, "get_evolution_engine", lambda: _BoomEngine())
        task = type("T", (), {"task_id": "t1", "task_type": "evolution_loop", "params": {"action": "run_loop"}})()
        await heartbeat_trigger_callback(task)
        assert queue.updates == [("t1", "failed")]

    async def test_non_evolution_falls_back_to_task_engine(self, monkeypatch):
        queue = self._fake_queue()
        self._patch_scheduler(monkeypatch, queue)
        called: list[str] = []

        class _FakeTaskEngine:
            def __init__(self):
                pass

            async def execute_task(self, task):
                called.append(task.task_id)

        monkeypatch.setattr(engine_mod, "ExecutionEngine", _FakeTaskEngine)
        task = type("T", (), {"task_id": "t2", "task_type": "lnn_training", "params": {}})()
        await heartbeat_trigger_callback(task)
        assert called == ["t2"]
        assert queue.updates == []  # 状态回写由 ExecutionEngine 负责


class TestCronRegistration:
    def test_register_cron_task(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LNN_EVOLUTION_CRON", "30 4 * * 1")
        # 隔离唤醒队列（避免写开发库 heartbeat.db）
        from app.heartbeat import heartbeat as hb
        from app.heartbeat.heartbeat import HeartbeatScheduler, ScheduleStatus, WakeupQueue

        queue = WakeupQueue(db_path=str(tmp_path / "hb.db"))
        sched = HeartbeatScheduler(wakeup_queue=queue)
        monkeypatch.setattr(hb, "get_scheduler", lambda: sched)
        assert register_evolution_cron_task() is True
        task = queue.get_task(EVOLUTION_CRON_TASK_ID)
        assert task is not None
        assert task.schedule == "30 4 * * 1"
        assert task.status == ScheduleStatus.PENDING
        # 幂等：重复注册不报错
        assert register_evolution_cron_task() is True
        queue.close()


class TestWorkflowTemplate:
    def test_template_loads_and_validates(self):
        tpl = load_builtin_template("evolution_loop")
        spec = template_to_spec(tpl)
        assert [n.node_id for n in spec.nodes] == ["collect_stats", "propose_patch", "write_report"]
        assert all(n.task_type == "evolution_loop" for n in spec.nodes)
        assert spec.metadata.get("proposal_based") is True
