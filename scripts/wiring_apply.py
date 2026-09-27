#!/usr/bin/env python3
"""自动接线脚本 — 功能缺口 + 路线图全部新增文件接线（替代手工执行接线指南 v3）。

背景
----
DSH 沙箱内已存在文件 write/edit 全部被拒（ReplaceFileW EACCES），无法在会话内完成
接线。本脚本用系统 Python 直接操作文件系统（不受 DSH 沙箱限制），自动执行：

    - 接线指南 v3：docs/development/接线指南-v3-总集成.md
    - 2 个路径 BUG：docs/development/预检发现-修复清单.md

用法
----
    py -3.11 scripts/wiring_apply.py               # dry-run：预览全部改动（默认，不改文件）
    py -3.11 scripts/wiring_apply.py --apply       # 实际执行（每个修改文件先写 .bak 备份）

步骤
----
    S0  修复 2 个测试路径 BUG（预检发现-修复清单）
    S1  删除 4 个垃圾/错误文件
    S2  7 处 __init__ 导出（mtconnect / models / contracts + 4 个白盒模块）
    S3  路由注册（engineering.py 追加 3 个新路由）
    S4  委托接线（dialect registry.py 生命周期全自动 + 2 个 pipeline.py 自动加导入并打印指引）
    S5  bridge feed 物理修复（/1000.0 → /spindle_rpm）+ 测试断言同步（0.5 → 0.0625）
    S6  打印门禁验证命令（接线指南 §6）

幂等性
------
已应用的改动会检测到并跳过（已应用/跳过），重复运行安全。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_DIR = REPO_ROOT / "engineering" / "python"
SRC_DIR = REPO_ROOT / "engineering" / "src"

# 统计
_applied = 0
_skipped = 0
_errors: list[str] = []
_warnings: list[str] = []


def log(msg: str) -> None:
    # 输出统一用 ASCII 标记（[OK]/[ERR]/[SKIP]...），避免 GBK 控制台编码错误
    print(msg)


def section(title: str) -> None:
    log(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# 文件工具


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        return
    if path.exists():
        shutil.copy2(path, str(path) + ".bak")
    path.write_text(text, encoding="utf-8")


def patch_file(rel_path: str, old: str, new: str, desc: str, dry_run: bool) -> bool:
    """old 必须恰好出现一次；new 已存在则跳过（幂等）。"""
    global _applied, _skipped, _errors
    path = PY_DIR / rel_path
    if not path.exists():
        _errors.append(f"文件不存在: {rel_path}")
        log(f"  [ERR] 文件不存在: {rel_path}")
        return False
    text = read_text(path)
    if new in text:
        _skipped += 1
        log(f"  [SKIP] 已应用（跳过）: {desc}")
        return True
    count = text.count(old)
    if count != 1:
        _errors.append(f"{rel_path} 匹配 {count} 次（需恰好 1 次）: {desc}")
        log(f"  [ERR] 匹配 {count} 次（需恰好 1 次）: {desc}")
        log(f"     old 前 100 字符: {old[:100]!r}")
        return False
    if dry_run:
        _applied += 1
        log(f"  [OK] [dry-run] {desc}")
        return True
    write_text(path, text.replace(old, new), dry_run=False)
    _applied += 1
    log(f"  [OK] {desc}")
    return True


def append_block(rel_path: str, marker: str, block: str, desc: str, dry_run: bool) -> bool:
    """文件末尾追加代码块（marker 已存在则跳过）。"""
    global _applied, _skipped, _errors
    path = PY_DIR / rel_path
    if not path.exists():
        _errors.append(f"文件不存在: {rel_path}")
        log(f"  [ERR] 文件不存在: {rel_path}")
        return False
    text = read_text(path)
    if marker in text:
        _skipped += 1
        log(f"  [SKIP] 已包含（跳过）: {desc}")
        return True
    new_text = text.rstrip() + "\n\n" + block + "\n"
    if dry_run:
        _applied += 1
        log(f"  [OK] [dry-run] {desc}")
        return True
    write_text(path, new_text, dry_run=False)
    _applied += 1
    log(f"  [OK] {desc}")
    return True


def extend_all(rel_path: str, names: list[str], dry_run: bool) -> None:
    """若文件含 __all__，把新名字并入（幂等）。"""
    path = PY_DIR / rel_path
    if not path.exists():
        return
    text = read_text(path)
    m = re.search(r"(__all__\s*=\s*\[)(.*?)(\])", text, re.S)
    if not m:
        return
    head, inner, tail = m.group(1), m.group(2), m.group(3)
    missing = [n for n in names if n not in inner]
    if not missing:
        return
    joined = ",\n    ".join(f'"{n}"' for n in missing)
    if inner.strip():
        new_inner = inner.rstrip() + ",\n    " + joined + "\n"
    else:
        new_inner = inner + "\n    " + joined + "\n"
    new_text = text[: m.start()] + head + new_inner + tail + text[m.end() :]
    if dry_run:
        log(f"  [OK] [dry-run] 更新 __all__（+{len(missing)} 名）: {rel_path}")
        return
    write_text(path, new_text, dry_run=False)
    log(f"  [OK] 更新 __all__（+{len(missing)} 名）: {rel_path}")


def remove_file(rel_path: str, dry_run: bool) -> None:
    global _applied, _skipped, _errors
    path = PY_DIR / rel_path
    if not path.exists():
        _skipped += 1
        log(f"  [SKIP] 不存在（跳过）: {rel_path}")
        return
    if dry_run:
        _applied += 1
        log(f"  [OK] [dry-run] 删除: {rel_path}")
        return
    path.unlink()
    _applied += 1
    log(f"  [OK] 删除: {rel_path}")


# 步骤实现


def step0_fix_path_bugs(dry_run: bool) -> None:
    """S0：修复 2 个测试路径 BUG（预检发现-修复清单）。"""
    section("S0  修复 2 个测试路径 BUG（预检发现-修复清单）")
    patch_file(
        "tests/unit/test_dialect_declared_hooks_phaseE.py",
        "Path(__file__).resolve().parents[3]",
        "Path(__file__).resolve().parents[4]",
        "test_dialect_declared_hooks_phaseE.py: parents[3] → parents[4]（postprocessor-plugins 在仓库根）",
        dry_run,
    )
    patch_file(
        "tests/unit/test_sovereignty_ratio.py",
        "Path(__file__).resolve().parents[2]",
        "Path(__file__).resolve().parents[4]",
        "test_sovereignty_ratio.py: parents[2] → parents[4]（scripts 在仓库根）",
        dry_run,
    )


def step1_delete_junk(dry_run: bool) -> None:
    """S1：删除 4 个垃圾/错误文件。"""
    section("S1  删除 4 个垃圾/错误文件")
    remove_file("app/api/v1/experience.py", dry_run)
    remove_file("app/services/domain/cutting_experience_service.py", dry_run)
    remove_file("tests/unit/_write_probe_test.py", dry_run)
    remove_file("tests/unit/_edit_probe.py", dry_run)


def step2_init_exports(dry_run: bool) -> None:
    """S2：7 处 __init__ 导出。"""
    section("S2  7 处 __init__ 导出")

    # 2.1 mtconnect
    block = (
        "from app.integrations.mtconnect.streaming import (\n"
        "    MTConnectStreamServer, StreamEvent, AlertEvent, StreamConsumer, WebSocketAlertHandler,\n"
        ")\n"
        "from app.integrations.mtconnect.conditions import (\n"
        "    ConditionChecker, ChatterDetector, Alert, AlertCondition, AlertPriority, AlertType,\n"
        ")\n"
        "from app.integrations.mtconnect.experience_bridge import MTConnectExperienceBridge\n"
    )
    append_block(
        "app/integrations/mtconnect/__init__.py",
        "MTConnectStreamServer",
        block,
        "mtconnect/__init__.py 追加 streaming/conditions/experience_bridge 导出",
        dry_run,
    )
    extend_all(
        "app/integrations/mtconnect/__init__.py",
        [
            "MTConnectStreamServer",
            "StreamEvent",
            "AlertEvent",
            "StreamConsumer",
            "WebSocketAlertHandler",
            "ConditionChecker",
            "ChatterDetector",
            "Alert",
            "AlertCondition",
            "AlertPriority",
            "AlertType",
            "MTConnectExperienceBridge",
        ],
        dry_run,
    )

    # 2.2 database/models
    append_block(
        "app/database/models/__init__.py",
        "CuttingExperienceRecord",
        "from app.database.models.cutting_experience import CuttingExperienceRecord\n",
        "database/models/__init__.py 追加 CuttingExperienceRecord",
        dry_run,
    )
    extend_all("app/database/models/__init__.py", ["CuttingExperienceRecord"], dry_run)

    # 2.3 contracts
    block = (
        "from app.contracts.cutting_experience import (\n"
        "    CuttingParameters, CuttingResults, MachiningAnomaly, CuttingExperience,\n"
        "    ExperienceQuery, ExperienceStats,\n"
        ")\n"
    )
    append_block(
        "app/contracts/__init__.py",
        "ExperienceStats",
        block,
        "contracts/__init__.py 追加 cutting_experience 6 符号",
        dry_run,
    )
    extend_all(
        "app/contracts/__init__.py",
        [
            "CuttingParameters",
            "CuttingResults",
            "MachiningAnomaly",
            "CuttingExperience",
            "ExperienceQuery",
            "ExperienceStats",
        ],
        dry_run,
    )

    # 2.4 parametric_geometry（P1-2 白盒）
    block = (
        "from app.parametric_geometry._review_state_machine import (\n"
        "    ST_PENDING, ST_RUNNING, ST_STEP_GENERATED, ST_REVIEWED,\n"
        "    ST_SUCCEEDED, ST_FAILED, ST_CANCELLED,\n"
        "    can_execute, can_review, can_finalize, all_features_reviewed,\n"
        "    next_status_after_review, is_terminal,\n"
        ")\n"
    )
    append_block(
        "app/parametric_geometry/__init__.py",
        "_review_state_machine",
        block,
        "parametric_geometry/__init__.py 追加 P1-2 白盒导出",
        dry_run,
    )

    # 2.5 dxf（P1-3 白盒）
    block = (
        "from app.dxf._pipeline_stages import (\n"
        "    STAGES, StageKey, StageStatus,\n"
        "    stage_name, stage_failure_is_fatal, should_abort_after,\n"
        "    progress_of, summarize_pipeline,\n"
        ")\n"
    )
    append_block(
        "app/dxf/__init__.py",
        "_pipeline_stages",
        block,
        "dxf/__init__.py 追加 P1-3 白盒导出",
        dry_run,
    )

    # 2.6 postprocessor/dialect（P4-2 白盒）
    block = (
        "from app.postprocessor.dialect._lifecycle import (\n"
        "    DialectLifecycleStage, can_transition, assert_transition_allowed,\n"
        "    next_stage_after_success, next_stage_after_failure, can_discover, is_terminal,\n"
        ")\n"
    )
    append_block(
        "app/postprocessor/dialect/__init__.py",
        "DialectLifecycleStage",
        block,
        "postprocessor/dialect/__init__.py 追加 P4-2 白盒导出",
        dry_run,
    )

    # 2.7 api/routers（P4-1 白盒）
    block = (
        "from app.api.routers._route_registry import (\n"
        "    RouterSpec, validate_specs, register_routers, is_duplicate_registration, group_by_domain,\n"
        ")\n"
    )
    append_block(
        "app/api/routers/__init__.py",
        "RouterSpec",
        block,
        "api/routers/__init__.py 追加 P4-1 白盒导出",
        dry_run,
    )


def step3_route_registration(dry_run: bool) -> None:
    """S3：路由注册（engineering.py 追加 3 个新路由）。"""
    section("S3  路由注册（engineering.py 追加 experience/optimizer/monitor_ws）")

    # 3.1 导入追加
    patch_file(
        "app/api/routers/engineering.py",
        "    tools,\n)\n",
        "    tools,\n    experience_routes,\n    monitor_ws,\n    optimizer_routes,\n)\n",
        "engineering.py 导入追加 3 个新路由模块",
        dry_run,
    )

    # 3.2 register() 追加
    patch_file(
        "app/api/routers/engineering.py",
        "    app.include_router(postprocessor_dialects.router)\n",
        "    app.include_router(postprocessor_dialects.router)\n"
        "\n"
        "    # === 数据飞轮 / 参数优化 / 实时监控（功能缺口接线）===\n"
        "    app.include_router(experience_routes.router)\n"
        "    app.include_router(optimizer_routes.router)\n"
        "    app.include_router(monitor_ws.router)\n",
        "engineering.py register() 追加 3 个 include_router",
        dry_run,
    )


def step4_delegation(dry_run: bool) -> None:
    """S4：委托接线。"""
    section("S4  委托接线")

    # 4.3 dialect registry.py（全自动）
    log("\n[S4.3] postprocessor/dialect/registry.py 生命周期委托（自动）")
    patch_file(
        "app/postprocessor/dialect/registry.py",
        "from app.postprocessor.registry import PostProcessorRegistry\n",
        "from app.postprocessor.registry import PostProcessorRegistry\n"
        "from app.postprocessor.dialect._lifecycle import (\n"
        "    DialectLifecycleStage,\n"
        "    next_stage_after_failure,\n"
        "    next_stage_after_success,\n"
        ")\n",
        "registry.py 导入 _lifecycle",
        dry_run,
    )
    patch_file(
        "app/postprocessor/dialect/registry.py",
        "        self._compile_errors: dict[str, str] = {}\n",
        "        self._compile_errors: dict[str, str] = {}\n"
        "        self._stages: dict[str, DialectLifecycleStage] = {}\n",
        "registry.py __init__ 追加 _stages 生命周期字典",
        dry_run,
    )
    patch_file(
        "app/postprocessor/dialect/registry.py",
        "            self._declarations[declaration.id] = declaration\n            found.append(declaration.id)\n",
        "            self._declarations[declaration.id] = declaration\n"
        "            self._stages[declaration.id] = DialectLifecycleStage.DISCOVERED\n"
        "            found.append(declaration.id)\n",
        "registry.py discover() 记录 DISCOVERED 状态",
        dry_run,
    )
    patch_file(
        "app/postprocessor/dialect/registry.py",
        "                self._compiled_classes[dialect_id] = self.compiler.compile(declaration)\n",
        "                self._compiled_classes[dialect_id] = self.compiler.compile(declaration)\n"
        "                self._stages[dialect_id] = next_stage_after_success(\n"
        '                    self._stages.get(dialect_id, DialectLifecycleStage.DISCOVERED), "compile"\n'
        "                )\n",
        "registry.py compile_all() 成功后记录 COMPILED",
        dry_run,
    )
    patch_file(
        "app/postprocessor/dialect/registry.py",
        "                self._compile_errors[dialect_id] = str(e)\n                raise\n",
        "                self._compile_errors[dialect_id] = str(e)\n"
        "                self._stages[dialect_id] = next_stage_after_failure(\n"
        "                    self._stages.get(dialect_id, DialectLifecycleStage.DISCOVERED)\n"
        "                )\n"
        "                raise\n",
        "registry.py compile_all() 失败后记录 FAILED",
        dry_run,
    )
    patch_file(
        "app/postprocessor/dialect/registry.py",
        "            registry.register(dialect_id, cls)\n            count += 1\n",
        "            registry.register(dialect_id, cls)\n"
        "            self._stages[dialect_id] = next_stage_after_success(\n"
        '                self._stages.get(dialect_id, DialectLifecycleStage.COMPILED), "register"\n'
        "            )\n"
        "            count += 1\n",
        "registry.py register_to() 成功后记录 REGISTERED",
        dry_run,
    )
    # 新增 unregister / lifecycle_status 方法（插在 get_compile_errors 之前）
    patch_file(
        "app/postprocessor/dialect/registry.py",
        "    def get_compile_errors(self",
        "    def unregister(self, dialect_id: str, target: PostProcessorRegistry | None = None) -> bool:\n"
        '        """卸载方言（P4-2 生命周期：REGISTERED → UNREGISTERED）。"""\n'
        "        registry = target or PostProcessorRegistry()\n"
        '        if hasattr(registry, "unregister"):\n'
        "            try:\n"
        "                registry.unregister(dialect_id)\n"
        "            except Exception as exc:\n"
        '                logger.warning("方言卸载失败: %s", exc)\n'
        "                return False\n"
        "        else:\n"
        '            logger.warning("PostProcessorRegistry 不支持 unregister，仅更新状态")\n'
        "        self._stages[dialect_id] = next_stage_after_success(\n"
        '            self._stages.get(dialect_id, DialectLifecycleStage.REGISTERED), "unregister"\n'
        "        )\n"
        '        logger.info("方言已卸载: %s", dialect_id)\n'
        "        return True\n"
        "\n"
        "    def lifecycle_status(self, dialect_id: str) -> str:\n"
        '        """查询方言生命周期状态（P4-2）。"""\n'
        "        stage = self._stages.get(dialect_id)\n"
        '        return stage.value if stage else "unknown"\n'
        "\n"
        "    def get_compile_errors(self",
        "registry.py 新增 unregister() / lifecycle_status() 方法",
        dry_run,
    )

    # 4.1 parametric_geometry/pipeline.py：自动加导入 + 指引
    log("\n[S4.1] parametric_geometry/pipeline.py 委托（自动加导入 + 人工 3 处）")
    append_block(
        "app/parametric_geometry/pipeline.py",
        "_review_state_machine",
        "from app.parametric_geometry._review_state_machine import (\n"
        "    can_execute, can_review, can_finalize, all_features_reviewed,\n"
        "    next_status_after_review,\n"
        ")\n",
        "parametric pipeline.py 追加 P1-2 白盒导入",
        dry_run,
    )
    log(
        "  [NOTE] 需按 docs/development/parametric_geometry-白盒化.md 委托 3 处：\n"
        "     1) run_pipeline():           开头 `if not can_execute(task.status): raise ...`\n"
        "     2) review_step_feature():    开头 `if not can_review(task.status): raise ...`；\n"
        "        审核完成判定改用 all_features_reviewed([...]) + next_status_after_review(...)\n"
        "     3) finalize_step():          开头 `if not can_finalize(task.status): raise ...`"
    )

    # 4.2 dxf/pipeline.py：自动加导入 + 指引
    log("\n[S4.2] dxf/pipeline.py 委托（自动加导入 + 人工 3 处）")
    append_block(
        "app/dxf/pipeline.py",
        "_pipeline_stages",
        "from app.dxf._pipeline_stages import (\n"
        "    STAGES, stage_name, stage_failure_is_fatal, should_abort_after, summarize_pipeline,\n"
        ")\n",
        "dxf pipeline.py 追加 P1-3 白盒导入",
        dry_run,
    )
    log(
        "  [NOTE] 需按 docs/development/dxf-pipeline-六阶段声明化.md 委托 3 处：\n"
        "     1) 阶段名输出改用 stage_name(StageKey.X)\n"
        "     2) Stage3 失败降级判定改用 should_abort_after(StageKey.MODEL_CONVERT, failed=True)\n"
        "     3) 结果摘要改用 summarize_pipeline(statuses, success)"
    )


def step5_bridge_feed_fix(dry_run: bool) -> None:
    """S5：bridge feed 物理修复 + 测试断言同步。"""
    section("S5  bridge feed 物理修复 + 测试断言同步")
    patch_file(
        "app/integrations/mtconnect/experience_bridge.py",
        "        feed_mm_per_rev = (sample.feedrate or 0.0) / 1000.0\n",
        "        feed_mm_per_rev = (sample.feedrate or 0.0) / spindle_rpm\n",
        "experience_bridge.py: feed 换算 /1000.0 → /spindle_rpm（物理正确）",
        dry_run,
    )
    patch_file(
        "app/integrations/mtconnect/experience_bridge.py",
        "        # 可用时构造（feed_mm_per_rev 用 feedrate/1000 近似），否则丢弃。\n",
        "        # 可用时构造（feed_mm_per_rev = feedrate ÷ spindle_rpm），否则丢弃。\n",
        "experience_bridge.py: 注释同步（物理换算说明）",
        dry_run,
    )
    patch_file(
        "tests/unit/test_mtconnect_experience_bridge.py",
        "        assert exp.parameters.feed_mm_per_rev == 0.5\n",
        "        assert exp.parameters.feed_mm_per_rev == 0.0625  # 500 mm/min ÷ 8000 rpm\n",
        "test_mtconnect_experience_bridge.py: 断言 0.5 → 0.0625（500/8000）",
        dry_run,
    )


def step6_print_gate_commands() -> None:
    """S6：打印门禁验证命令（接线指南 §6）。"""
    section("S6  门禁验证命令（接线后执行）")
    log(
        "cd engineering/python\n"
        "$env:PYTHONUTF8=1; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1\n"
        "py -3.11 -m pytest tests/unit/test_mtconnect_monitoring.py "
        "tests/unit/test_cutting_experience_contract.py "
        "tests/unit/test_cutting_experience_repository.py "
        "tests/unit/test_mtconnect_experience_bridge.py "
        "tests/unit/test_optimizer_whitebox.py "
        "tests/unit/test_parametric_review_state_machine.py "
        "tests/unit/test_dxf_pipeline_stages.py "
        "tests/unit/test_sovereignty_ratio.py "
        "tests/unit/test_route_registry.py "
        "tests/unit/test_dialect_lifecycle.py "
        "tests/api/test_optimizer_api.py tests/api/test_monitor_ws.py "
        "--no-header -q -o addopts=''\n"
        "ruff check app/\n"
        "py -3.11 -m mypy --config-file mypy.ini app/parametric_geometry/_review_state_machine.py "
        "app/dxf/_pipeline_stages.py app/api/routers/_route_registry.py "
        "app/postprocessor/dialect/_lifecycle.py app/optimizer/\n"
        "cd ../../src && npx vitest run composables/__tests__/useDataTable.test.ts "
        "stores/__tests__/defineCrudStore.test.ts router/__tests__/createAppRouter.test.ts "
        "api/__tests__/parameterOptimizer.test.ts api/__tests__/cuttingExperience.test.ts "
        "stores/__tests__/experienceStore.test.ts components/__tests__/MachineMonitor.test.ts "
        "components/__tests__/ExperienceCapture.test.ts components/__tests__/ParameterRecommendPanel.test.ts\n"
        "npx vue-tsc --noEmit\n"
        "py -3.11 ../../scripts/sovereignty_ratio.py --target 0.50"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="自动接线脚本（接线指南 v3 + 修复清单）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行（默认 dry-run 预览；执行时每个文件先备份 .bak）",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        log("DRY-RUN 模式：仅预览改动，不修改任何文件。加 --apply 实际执行。")
    else:
        log("APPLY 模式：实际执行接线（每个修改文件先写 .bak 备份）。")

    log(f"仓库根: {REPO_ROOT}")
    log(f"Python 目录: {PY_DIR}")

    step0_fix_path_bugs(dry_run)
    step1_delete_junk(dry_run)
    step2_init_exports(dry_run)
    step3_route_registration(dry_run)
    step4_delegation(dry_run)
    step5_bridge_feed_fix(dry_run)
    step6_print_gate_commands()

    section("结果汇总")
    log(f"  [OK] 应用/将应用: {_applied}")
    log(f"  [SKIP] 已存在跳过: {_skipped}")
    if _errors:
        log(f"  [ERR] 错误: {len(_errors)}")
        for err in _errors:
            log(f"     - {err}")
    else:
        log("  [OK] 无错误")
    if _warnings:
        log(f"  [WARN] 警告: {len(_warnings)}")

    if dry_run and _applied:
        log("\n预览完成：运行 `py -3.11 scripts/wiring_apply.py --apply` 实际执行接线。")
    if _errors:
        log("\n存在未应用的改动（见上方 [ERR]），修复后重新运行。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
