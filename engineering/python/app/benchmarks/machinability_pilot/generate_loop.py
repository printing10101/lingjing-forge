"""Pilot 生成闭环：LLM 生成 CadQuery 脚本 → S1 沙箱执行 → S2 B-rep 校验 → S3 几何指标。

与 app/cad/nl2cad_llm 的区别：nl2cad 的系统提示词把模型限制在基础体素
（box/cylinder/sphere/cone），对本基准的曲面案例（loft/sweep/twist）不公平。
本模块复用其执行与校验层（CadQueryGenerator / validate_exported_model /
extract_code / AST 审计），但换用允许全部曲面 API 的基准系统提示词。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cadquery as cq

from app.benchmarks.machinability_pilot.metrics_3d import (
    VOXEL_RESOLUTION,
    chamfer_distance,
    load_mesh,
    normalize_to_unit_box,
    voxel_iou,
)
from app.benchmarks.machinability_pilot.grounding import (
    format_static_block,
    load_api_cards,
    load_idiom_cards,
    repair_knowledge,
    select_static_cards,
)
from app.benchmarks.machinability_pilot.llm_backends import OpenAICompatBackend
from app.cad._brep_validator import validate_exported_model
from app.cad.cadquery_gen import CadQueryError, CadQueryGenerator, CadQueryScriptError
from app.cad.nl2cad_llm import _run_ast_audit, extract_code

PILOT_SYSTEM_PROMPT = (
    "You are an expert CAD programmer. Write a CadQuery (Python) script that builds the "
    "3D solid described by the user.\n"
    "Hard constraints:\n"
    "1. The `cq` (cadquery) module is pre-injected; never import anything.\n"
    "2. Any cq.Workplane API is allowed, including loft, sweep, revolve, twistExtrude, "
    "shell, fillet, chamfer, cut, union — choose whatever matches the described geometry.\n"
    "3. All dimensions must be finite positive numbers in mm.\n"
    "4. Assign the final solid to a variable named `result` at the end of the script.\n"
    "5. Output ONLY Python code, no markdown fences, no explanations."
)


# 沙箱预注入 cq 并禁止任何 import；主流模型（DeepSeek 实测）习惯性写
# `import cadquery as cq`，剥离这两类安全行让 AST 审计度量的是几何能力
# 而非模板遵从性。其余 import（os/sys 等）原样保留给审计器拒绝。
_SAFE_IMPORT_RE = re.compile(
    r"^\s*import\s+(cadquery(?:\s+as\s+\w+)?|math(?:\s+as\s+\w+)?)\s*$|"
    r"^\s*from\s+cadquery\s+import\s+[\w, *]+\s*$",
    re.MULTILINE,
)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def clean_llm_text(text: str) -> str:
    """剥掉思考型模型的 <think> 段，避免推理文本混进代码提取。"""
    return _THINK_RE.sub("", text)


def strip_safe_imports(script: str) -> str:
    return _SAFE_IMPORT_RE.sub("", script).lstrip("\n")


# ---------------------------------------------------------------- relaxed harness

_RELAXED_AUDIT_WHITELIST = {"cadquery", "cq", "math"}


def audit_relaxed(script: str) -> None:
    """relaxed 口径审计：语法 + import 白名单（cadquery/math）+ 危险属性。

    白名单内的 import 由执行环境预注入模块承接，其余 import 拒绝。
    """
    import ast as _ast

    tree = _ast.parse(script)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _RELAXED_AUDIT_WHITELIST:
                    raise CadQueryScriptError(f"relaxed 口径仅允许 import cadquery/math，拒绝: {alias.name}")
        elif isinstance(node, _ast.ImportFrom):
            if (node.module or "").split(".")[0] not in _RELAXED_AUDIT_WHITELIST:
                raise CadQueryScriptError(f"relaxed 口径仅允许 from cadquery/math import，拒绝: {node.module}")
    _run_ast_audit(strip_all_imports(script))


def strip_all_imports(script: str) -> str:
    lines = [line for line in script.splitlines() if not line.strip().startswith(("import ", "from "))]
    return "\n".join(lines)


_RELAXED_BOOTSTRAP = """\
import json, sys
meta = json.loads(sys.stdin.readline())
sys.path.insert(0, meta["python_root"])
import cadquery as cq
import math
try:
    g = {"cq": cq, "cadquery": cq, "math": math, "__builtins__": __builtins__}
    exec(meta["script"], g)
    cq.exporters.export(g["result"], meta["out_path"], exportType=cq.exporters.ExportTypes.STEP)
except BaseException as exc:
    print(json.dumps({"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}))
else:
    print(json.dumps({"status": "ok"}))
"""


def _run_relaxed(script: str, out_path: Path, timeout_s: float) -> None:
    """relaxed 口径执行：预注入 cq/math 的子进程，白名单外无沙箱限制（消融条件专用）。"""
    envelope = json.dumps(
        {
            "script": script,
            "out_path": str(out_path),
            "python_root": str(Path(__file__).resolve().parents[2]),
        }
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RELAXED_BOOTSTRAP],
            input=envelope,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        raise CadQueryScriptError(f"relaxed 执行超时 ({timeout_s:.0f}s)") from e
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            if parsed.get("status") == "ok":
                return
            raise CadQueryScriptError(f"relaxed 执行失败: {parsed.get('error')}")
        break
    raise CadQueryScriptError(f"relaxed 子进程未按协议回传 (exit={proc.returncode})")


def build_pilot_prompt(description: str, feedback: str | None = None, knowledge: str | None = None) -> str:
    prompt = f"Build the following part as a CadQuery script:\n\n{description}\n"
    if knowledge:
        prompt += f"\n{knowledge}\n"
    if feedback:
        prompt += (
            f"\nThe previous attempt failed:\n{feedback}\nFix the problem and regenerate. Output only Python code."
        )
    return prompt


@dataclass
class AttemptTrace:
    attempt: int
    stage_failed: str | None = None  # ast / exec / brep / None(成功)
    error: str | None = None


@dataclass
class GenerationRecord:
    case_id: str
    backend: str
    ok: bool
    attempts: int
    stage_failed: str | None
    last_error: str | None
    harness: str = "strict"
    grounding: list[str] = field(default_factory=list)
    static_cards: int = 0
    feedback_used: list[str] = field(default_factory=list)
    traces: list[AttemptTrace] = field(default_factory=list)
    script: str | None = None
    # S3 指标（成功时）
    chamfer_x1e3: float | None = None
    voxel_iou: float | None = None
    voxel_filled: bool | None = None
    duration_s: float = 0.0
    llm_latency_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["traces"] = [asdict(t) for t in self.traces]
        return data


def _export_stl_from_step(step_path: Path, stl_path: Path) -> None:
    shape = cq.importers.importStep(str(step_path))
    cq.exporters.export(shape, str(stl_path), tolerance=0.05, angularTolerance=0.2)


async def generate_for_case(
    case_id: str,
    description: str,
    truth_stl: Path,
    backend: OpenAICompatBackend,
    run_dir: Path,
    max_attempts: int = 3,
    voxel_resolution: int = VOXEL_RESOLUTION,
    harness: str = "strict",
    grounding: frozenset[str] | None = None,
) -> GenerationRecord:
    """对单个案例跑生成闭环，产物（脚本/STEP/STL）落 run_dir/case_id/。

    harness="strict"：仓库沙箱（禁 import、预注入 cq）；"relaxed"：预注入
    cq/math 的白名单执行环境（消融条件，量化沙箱契约对成功率的影响）。
    grounding：方法组件开关（api/skills/typed_feedback），消融用。
    """
    grounding = grounding or frozenset()
    case_dir = run_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    generator = CadQueryGenerator()
    record = GenerationRecord(
        case_id=case_id,
        backend=backend.name,
        ok=False,
        attempts=0,
        stage_failed=None,
        last_error=None,
        harness=harness,
        grounding=sorted(grounding),
    )

    api_cards = load_api_cards() if grounding else {}
    idiom_cards = load_idiom_cards() if grounding else {}
    static_block = ""
    if grounding & {"api", "skills"}:
        selected = select_static_cards(description, api_cards, idiom_cards)
        if "api" not in grounding:
            selected["api"] = []
        if "skills" not in grounding:
            selected["idioms"] = []
        static_block = format_static_block(selected)
        record.static_cards = len(selected["api"]) + len(selected["idioms"])

    def _feedback(error: str) -> str:
        if "typed_feedback" not in grounding:
            return error
        knowledge = repair_knowledge(error, api_cards, idiom_cards)
        return f"{error}\n\n{knowledge}" if knowledge else error

    for attempt in range(1, max_attempts + 1):
        record.attempts = attempt
        prompt = build_pilot_prompt(
            description,
            feedback=record.feedback_used[-1] if record.feedback_used else None,
            knowledge=static_block or None,
        )
        t0 = time.perf_counter()
        try:
            text = await backend.call(prompt, system=PILOT_SYSTEM_PROMPT)
        except Exception as exc:  # 网络/配额等 LLM 层失败：不重试生成，直接终止本次案例
            record.last_error = f"llm_call: {type(exc).__name__}: {exc}"
            record.stage_failed = "llm"
            record.traces.append(AttemptTrace(attempt, "llm", record.last_error))
            break
        record.llm_latency_s += time.perf_counter() - t0
        script = (
            extract_code(clean_llm_text(text))
            if harness == "relaxed"
            else strip_safe_imports(extract_code(clean_llm_text(text)))
        )
        (case_dir / f"script_attempt{attempt}.py").write_text(script, encoding="utf-8")

        trace = AttemptTrace(attempt=attempt)

        # S1 前置：AST 审计（语法 / import 策略 / 危险属性）
        try:
            if harness == "relaxed":
                audit_relaxed(script)
            else:
                _run_ast_audit(script)
        except (SyntaxError, CadQueryScriptError) as exc:
            trace.stage_failed, trace.error = "ast", f"{type(exc).__name__}: {exc}"
            record.traces.append(trace)
            record.feedback_used.append(_feedback(str(exc)))
            record.stage_failed, record.last_error = "ast", trace.error
            continue

        # S1：沙箱执行 + 导出 STEP
        task_id = f"pilot_{case_id}_a{attempt}"
        try:
            if harness == "relaxed":
                step_path = case_dir / f"model_attempt{attempt}.step"
                timeout_s = max(60.0, float(os.environ.get("LNN_CADQUERY_TIMEOUT", "120")))
                _run_relaxed(script, step_path, timeout_s)
            else:
                step_path = Path(await generator.execute_and_export(script, task_id, "step"))
        except (CadQueryError, ValueError, TypeError, OSError, RuntimeError) as exc:
            trace.stage_failed, trace.error = "exec", f"{type(exc).__name__}: {exc}"
            record.traces.append(trace)
            record.feedback_used.append(_feedback(f"CadQuery 执行/导出失败: {exc}"))
            record.stage_failed, record.last_error = "exec", trace.error
            continue

        # S2：B-rep 拓扑校验
        report = validate_exported_model(str(step_path), "step")
        if report is not None and report.errors:
            codes = list(report.error_codes)
            trace.stage_failed, trace.error = "brep", f"{codes}: {'; '.join(i.message for i in report.errors)}"
            record.traces.append(trace)
            record.feedback_used.append(_feedback(trace.error))
            record.stage_failed, record.last_error = "brep", trace.error
            continue

        # 成功：产物落 run_dir，S3 几何指标
        trace.stage_failed = None
        record.traces.append(trace)
        record.script = script
        record.stage_failed = None
        record.last_error = None  # 清掉历史尝试的报错；指标失败会在下方重新赋值
        record.ok = True
        final_step = case_dir / "model.step"
        shutil.copyfile(step_path, final_step)
        final_stl = case_dir / "model.stl"
        _export_stl_from_step(final_step, final_stl)
        try:
            truth = normalize_to_unit_box(load_mesh(truth_stl))
            cand = normalize_to_unit_box(load_mesh(final_stl))
            record.chamfer_x1e3 = chamfer_distance(truth, cand)
            record.voxel_iou, record.voxel_filled = voxel_iou(truth, cand, resolution=voxel_resolution)
        except Exception as exc:  # 指标失败不推翻 S1/S2 成功，如实标注
            record.last_error = f"metrics: {type(exc).__name__}: {exc}"
        break

    record.duration_s = time.perf_counter() - started
    return record
