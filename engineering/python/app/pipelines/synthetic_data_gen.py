"""飞轮合成数据生成器（W4.1：仿真合成数据先行，"不等真机也能动的数据源"）。

《产品叙事与战略对标-2026-09》W4 的落地：飞轮全链路管道都在、缺的是水——
真机未接、经验库零冷启动。本模块用**体素仿真 + 切削力模型**做参数扫描
（材料 × 转速 × 进给 × 切深），批量生成"参数 → 仿真结果"样本对，经
``DatasetStore`` 提交为不可变数据集版本（带血缘），写入即进入飞轮训练侧
消费通道。

每个样本：
- ``params``：材料 / 刀具 / 转速 / 进给 / 切深
- ``gcode``：按参数合成的简单铣削程序（多层直线下切）
- ``voxel``：体素校验报告（过切 / 快速段切入材料检测，passed/severity）
- ``force``：切削力预测（PINN，无 torch 自动降级 Kienzle 解析解）

设计要点：
- **零 torch 硬依赖**：切削力走自动降级链；体素校验 Rust 内核可选加速；
- 故意不规避越界参数——撞刀/过切样本正是"敢上机"分类器的正样本，
  voxel_passed=False 的记录与通过记录同权重落库；
- 数据集为一次性 commit 的不可变版本（内容寻址去重），血缘记录生成参数。

开关：``LNN_FLYWHEEL_SYNTHETIC_ENABLED``（默认开启）。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config.cam_validation import CamValidationConfig
from app.simulation.cutting_force.predictor import predict_cutting_force

logger = logging.getLogger(__name__)

#: 单次生成 combos 上限（防止 REST 调用打爆 CPU / 拉长事件循环）
MAX_COMBINATIONS = 200
#: 合成程序的切削层数
MAX_PASSES = 3

DATASET_SCHEMA_FIELDS: dict[str, dict[str, Any]] = {
    "sample_id": {"type": "str", "required": True, "description": "样本唯一 ID"},
    "material": {"type": "str", "required": True, "description": "材料（如 45steel）"},
    "tool": {"type": "str", "required": True, "description": "刀具（如 endmill_d10）"},
    "spindle_rpm": {"type": "float", "required": True, "description": "主轴转速"},
    "feed_rate": {"type": "float", "required": True, "description": "进给 mm/min"},
    "depth_of_cut": {"type": "float", "required": True, "description": "切深 mm"},
    "gcode": {"type": "str", "required": True, "description": "合成 G 代码程序"},
    "voxel_passed": {"type": "bool", "required": True, "description": "体素校验是否通过"},
    "voxel_severity": {"type": "str", "required": True, "description": "none/warning/critical"},
    "collision_count": {"type": "int", "required": True, "description": "碰撞块数"},
    "force_Fx": {"type": "float", "required": True, "description": "切削力 X 分量 N"},
    "force_Fy": {"type": "float", "required": True, "description": "切削力 Y 分量 N"},
    "force_Fz": {"type": "float", "required": True, "description": "切削力 Z 分量 N"},
    "force_method": {"type": "str", "required": True, "description": "pinn/kienzle 降级链方法"},
    "generated_at": {"type": "str", "required": True, "description": "生成时间 ISO8601"},
}

_ENV_ENABLED = "LNN_FLYWHEEL_SYNTHETIC_ENABLED"


def synthetic_enabled() -> bool:
    return os.getenv(_ENV_ENABLED, "1").strip().lower() not in ("0", "false", "no", "off")


def build_synth_gcode(
    rpm: float,
    feed: float,
    depth: float,
    stock_length: float,
    stock_width: float,
    stock_height: float,
    safe_z: float = 25.0,
    stock_top_z: float = 0.0,
) -> str:
    """按参数合成简单多层直线下切铣削程序（Fanuc 方言）。"""
    total_depth = min(max(depth, 0.1) * MAX_PASSES, stock_height * 0.9)
    per_pass = total_depth / MAX_PASSES
    x_end = max(stock_length * 0.6, 5.0)
    y_end = max(stock_width * 0.4, 5.0)
    lines = [
        "O1001",
        "G90 G54",
        f"S{max(int(rpm), 1)} M03",
        f"G00 Z{safe_z:.3f}",
        "G00 X0 Y0",
        f"G00 Z{stock_top_z:.3f}",
    ]
    z = stock_top_z
    for _ in range(MAX_PASSES):
        z -= per_pass
        lines.append(f"G01 Z{z:.3f} F{feed:g}")
        lines.append(f"G01 X{x_end:.3f} F{feed:g}")
        lines.append(f"G01 Y{y_end:.3f} F{feed:g}")
        lines.append(f"G00 Z{safe_z:.3f}")
        lines.append("G00 X0 Y0")
        lines.append(f"G00 Z{z:.3f}")
    lines.append("G00 Z50.0")
    lines.append("M05")
    lines.append("M30")
    return "\n".join(lines) + "\n"


@dataclass
class SyntheticGenSummary:
    """生成结果汇总。"""

    dataset_id: str | None = None
    version: str | None = None
    total: int = 0
    voxel_passed: int = 0
    voxel_failed: int = 0
    duration_seconds: float = 0.0
    combos_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "total": self.total,
            "voxel_passed": self.voxel_passed,
            "voxel_failed": self.voxel_failed,
            "duration_seconds": round(self.duration_seconds, 2),
            "combos_skipped": self.combos_skipped,
            "errors": self.errors,
        }


def _validate_voxel(gcode: str, stock: dict[str, float]) -> dict[str, Any]:
    from app.cam_validation.voxel_validator import VoxelValidator

    validator = VoxelValidator(CamValidationConfig())
    report = validator.validate(
        gcode_text=gcode,
        controller_type="fanuc_0i",
        safe_z=25.0,
        stock_top_z=0.0,
        stock_length=float(stock.get("length", 100.0)),
        stock_width=float(stock.get("width", 100.0)),
        stock_height=float(stock.get("height", 30.0)),
    )
    return report.to_dict()


async def _generate_sample(
    material: str,
    tool: str,
    rpm: float,
    feed: float,
    depth: float,
    stock: dict[str, float],
    use_pinn: bool,
) -> dict[str, Any]:
    """生成单个"参数→仿真结果"样本（CPU 密集部分走线程池）。"""
    from datetime import datetime, timezone

    gcode = build_synth_gcode(
        rpm,
        feed,
        depth,
        stock_length=float(stock.get("length", 100.0)),
        stock_width=float(stock.get("width", 100.0)),
        stock_height=float(stock.get("height", 30.0)),
    )
    voxel = await asyncio.to_thread(_validate_voxel, gcode, stock)
    force = await asyncio.to_thread(
        predict_cutting_force,
        material,
        tool,
        {"speed": rpm, "feed": feed, "depth": depth},
        use_pinn,
    )
    return {
        "sample_id": f"synth_{uuid.uuid4().hex[:12]}",
        "material": material,
        "tool": tool,
        "spindle_rpm": float(rpm),
        "feed_rate": float(feed),
        "depth_of_cut": float(depth),
        "gcode": gcode,
        "voxel_passed": bool(voxel.get("passed", False)),
        "voxel_severity": str(voxel.get("severity", "none")),
        "collision_count": int(voxel.get("collision_count", 0)),
        "force_Fx": float(force.get("Fx", 0.0)),
        "force_Fy": float(force.get("Fy", 0.0)),
        "force_Fz": float(force.get("Fz", 0.0)),
        "force_method": str(force.get("method", "kienzle")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def generate_synthetic_dataset(
    *,
    material: str = "45steel",
    tool: str = "endmill_d10",
    rpm_values: list[float] | None = None,
    feed_values: list[float] | None = None,
    depth_values: list[float] | None = None,
    stock: dict[str, float] | None = None,
    dataset_name: str = "synthetic_machining_params_v1",
    owner_id: str = "system",
    use_pinn: bool = False,
    max_combinations: int = MAX_COMBINATIONS,
) -> SyntheticGenSummary:
    """参数扫描批量生成"参数→仿真结果"样本对并提交为数据集版本。

    Args:
        material/tool: 材料与刀具标识（透传切削力预测器）
        rpm_values/feed_values/depth_values: 扫描网格（默认各 3 档）
        stock: 毛坯尺寸 {length,width,height}
        dataset_name: 数据集名（同 name 重复生成时新增版本）
        use_pinn: 切削力是否优先 PINN（无 torch 自动降级）
        max_combinations: 网格上限（超出部分跳过并计数）

    Returns:
        SyntheticGenSummary（含 dataset_id / version / 通过率）
    """
    start = time.perf_counter()
    summary = SyntheticGenSummary()
    # P2-12：入口统一 float 强转——非数值档位在此报错，而不是混进
    # records 后在错误路径的 f-string 格式化里二次异常逃出整批保护
    try:
        rpm_values = [float(v) for v in (rpm_values or [2000.0, 4000.0, 6000.0])]
        feed_values = [float(v) for v in (feed_values or [200.0, 400.0, 800.0])]
        depth_values = [float(v) for v in (depth_values or [0.5, 1.0, 2.0])]
        stock = {k: float(v) for k, v in (stock or {"length": 100.0, "width": 100.0, "height": 30.0}).items()}
    except (TypeError, ValueError) as e:
        raise ValueError(f"扫描档位必须为数值: {e}") from e
    stock = stock or {"length": 100.0, "width": 100.0, "height": 30.0}

    combos = list(itertools.product(rpm_values, feed_values, depth_values))
    if len(combos) > max_combinations:
        summary.combos_skipped = len(combos) - max_combinations
        combos = combos[:max_combinations]

    records: list[dict[str, Any]] = []
    for rpm, feed, depth in combos:
        try:
            sample = await _generate_sample(material, tool, rpm, feed, depth, stock, use_pinn)
        except Exception as e:  # 单样本失败不拖垮整批
            logger.warning("合成样本生成失败 (rpm=%s feed=%s depth=%s): %s", rpm, feed, depth, e)
            summary.errors.append(f"rpm={rpm:g},feed={feed:g},depth={depth:g}: {type(e).__name__}")
            continue
        if sample["voxel_passed"]:
            summary.voxel_passed += 1
        else:
            summary.voxel_failed += 1
        records.append(sample)

    summary.total = len(records)
    summary.duration_seconds = time.perf_counter() - start

    if records:
        from app.contracts.dataset import DatasetSchema, LineageRecord
        from app.data.dataset_store import get_dataset_store

        store = get_dataset_store()
        schema = DatasetSchema(fields=dict(DATASET_SCHEMA_FIELDS), primary_key=["sample_id"])
        # 重名数据集复用 id（create 对重名抛 ValueError）——重复生成追加
        # 版本而非崩溃，保证飞轮可持续运转
        dataset_id = None
        try:
            for ds in await store.list_datasets(limit=1000):
                if ds.get("name") == dataset_name:
                    dataset_id = ds.get("id")
                    break
        except (ValueError, RuntimeError, OSError) as e:
            logger.debug("查询既有数据集失败（尝试直接创建）: %s", e)
        if dataset_id is None:
            try:
                dataset_id = await store.create(
                    dataset_name,
                    schema,
                    owner_id=owner_id,
                    description="体素仿真+切削力模型参数扫描合成数据（W4.1 飞轮充能）",
                )
            except ValueError:
                # 并发窗口内被他人创建：回查复用
                for ds in await store.list_datasets(limit=1000):
                    if ds.get("name") == dataset_name:
                        dataset_id = ds.get("id")
                        break
                if dataset_id is None:
                    raise
        version_contract = await store.commit_version(
            dataset_id,
            records,
            lineage=LineageRecord(
                record_id=f"lineage_{uuid.uuid4().hex[:12]}",
                target=f"dataset://{dataset_name}",
                source_type="manual",
                source_ref=f"synthetic_gen:{uuid.uuid4().hex[:8]}",
                inputs=[],
                outputs=[f"dataset://{dataset_name}"],
                operation="augment",
                metadata={
                    "material": material,
                    "tool": tool,
                    "grid": {
                        "rpm": rpm_values,
                        "feed": feed_values,
                        "depth": depth_values,
                    },
                    "use_pinn": use_pinn,
                },
            ),
        )
        summary.dataset_id = dataset_id
        summary.version = getattr(version_contract, "version", None)

    logger.info(
        "合成数据生成完成：total=%d passed=%d failed=%d dataset=%s/%s (耗时 %.1fs)",
        summary.total,
        summary.voxel_passed,
        summary.voxel_failed,
        summary.dataset_id,
        summary.version,
        summary.duration_seconds,
    )
    return summary
