"""材料解析器：从 materials.json 加载材料切削参数基线。

设计原则
========
灵境制造的切削参数推荐模块采用「材料优先」策略：
- 材料的 specific_cutting_force (K_s) 直接决定颤振预测（阶段 5）的稳定性极限
- 材料的 cutting_speed_range / feed_range / depth_of_cut_range 决定推荐参数的上下界
- Taylor 指数 (n) 与常数 (C) 用于刀具寿命估算

数据来源（项目记忆硬约束）：
- 复用 python/app/database/data/materials.json 中已有的 17 种材料（TC4 / 6061-T6 等）
- 补齐 HRC52 淬火钢数据缺口（项目自采工业数据核心组成）
- HRC52 数据标注「待自采数据校准」，避免误用纯文献值

不耦合 database 模块的内部接口：
- 直接读取 materials.json（json 模块），便于独立测试
- 补齐的 HRC52 数据通过内存覆盖，不修改原始 JSON 文件（避免污染共享数据源）

工业硬约束（项目记忆）：
- 材料参数必须基于自采工业数据校准，HRC52 不可使用纯文献数据
- 阶段 5 Tlusty 解析法需要 K_s（specific_cutting_force），必须按材料校准
- 系统定位「工程师助手」，材料参数推荐后必须经工程师审核
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 材料数据类


@dataclass
class MaterialParams:
    """材料切削参数基线。

    所有切削参数范围均为 [min, max] 二元组，单位：
    - cutting_speed_range: m/min（米/分钟）
    - feed_range: mm/tooth（毫米/齿）
    - depth_of_cut_range: mm（毫米）
    - specific_cutting_force: N/mm²（即 K_s，用于阶段 5 颤振预测）
    """

    id: str
    name: str
    category: str
    hardness_hb: float
    tensile_strength_mpa: float
    thermal_conductivity: float  # W/(m·K)
    density_gcm3: float
    specific_cutting_force: float  # K_s (N/mm²)
    cutting_speed_range: dict[str, list[float]]  # {roughing: [min,max], finishing: [min,max]}
    feed_range: dict[str, list[float]]
    depth_of_cut_range: dict[str, list[float]]
    taylor_exponent_n: float
    taylor_constant_c: float
    hardness_hrc: float | None = None  # 淬火钢专用，HRC52 等使用
    data_source: str = "database"  # database / hrc52_supplement / external
    calibration_status: str = "calibrated"  # calibrated / pending_calibration

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "hardness_hb": self.hardness_hb,
            "hardness_hrc": self.hardness_hrc,
            "tensile_strength_mpa": self.tensile_strength_mpa,
            "thermal_conductivity": self.thermal_conductivity,
            "density_gcm3": self.density_gcm3,
            "specific_cutting_force": self.specific_cutting_force,
            "cutting_speed_range": self.cutting_speed_range,
            "feed_range": self.feed_range,
            "depth_of_cut_range": self.depth_of_cut_range,
            "taylor_exponent_n": self.taylor_exponent_n,
            "taylor_constant_c": self.taylor_constant_c,
            "data_source": self.data_source,
            "calibration_status": self.calibration_status,
        }


# HRC52 淬火钢补充数据


_HRC52_SUPPLEMENT = MaterialParams(
    id="steel_hrc52",
    name="HRC52淬火钢",
    category="hardened_steel",
    hardness_hb=495.0,  # HRC52 ≈ HB495（标准换算表）
    hardness_hrc=52.0,
    tensile_strength_mpa=1800.0,
    thermal_conductivity=25.0,  # 淬火钢导热性较差
    density_gcm3=7.85,
    specific_cutting_force=2800.0,  # 淬火钢切削力大，用于阶段 5 颤振预测
    # 淬火钢切削速度低，需硬质合金或陶瓷刀具
    cutting_speed_range={
        "roughing": [20.0, 40.0],
        "finishing": [40.0, 80.0],
    },
    feed_range={
        "roughing": [0.05, 0.15],
        "finishing": [0.02, 0.08],
    },
    depth_of_cut_range={
        "roughing": [0.5, 2.0],
        "finishing": [0.05, 0.3],
    },
    taylor_exponent_n=0.12,  # 淬火钢 Taylor 指数较低
    taylor_constant_c=80.0,
    data_source="hrc52_supplement",
    calibration_status="pending_calibration",  # 待自采数据校准
)


# 材料解析器


class MaterialResolverError(Exception):
    """材料解析器异常。"""


class MaterialResolver:
    """材料解析器：加载 materials.json + HRC52 补充数据。

    使用方式：
        resolver = MaterialResolver()
        material = resolver.get_material("ti_tc4")  # TC4 钛合金
        material = resolver.get_material("steel_hrc52")  # HRC52 淬火钢

    数据源优先级：
        1. 内存 HRC52 补充数据（_HRC52_SUPPLEMENT）
        2. materials.json 数据库（TC4 / 6061-T6 等 17 种）
        3. 未找到时抛出 MaterialResolverError
    """

    def __init__(self, materials_json_path: str | Path | None = None) -> None:
        if materials_json_path is None:
            # 默认路径：python/app/database/data/materials.json
            project_root = Path(__file__).resolve().parents[3]
            self._materials_json_path = project_root / "app" / "database" / "data" / "materials.json"
        else:
            self._materials_json_path = Path(materials_json_path)

        self._database_materials: dict[str, MaterialParams] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """懒加载 materials.json（首次调用时加载）。"""
        if self._loaded:
            return

        try:
            with open(self._materials_json_path, encoding="utf-8") as f:
                raw_list = json.load(f)
        except FileNotFoundError as e:
            logger.warning(
                "materials.json 未找到 %s，仅 HRC52 补充数据可用: %s",
                self._materials_json_path,
                e,
            )
            raw_list = []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("materials.json 解析失败: %s", e)
            raw_list = []

        for raw in raw_list:
            try:
                params = MaterialParams(
                    id=str(raw["id"]),
                    name=str(raw["name"]),
                    category=str(raw["category"]),
                    hardness_hb=float(raw["hardness_hb"]),
                    tensile_strength_mpa=float(raw["tensile_strength_mpa"]),
                    thermal_conductivity=float(raw["thermal_conductivity"]),
                    density_gcm3=float(raw["density_gcm3"]),
                    specific_cutting_force=float(raw["specific_cutting_force"]),
                    cutting_speed_range=self._parse_range(raw["cutting_speed_range"]),
                    feed_range=self._parse_range(raw["feed_range"]),
                    depth_of_cut_range=self._parse_range(raw["depth_of_cut_range"]),
                    taylor_exponent_n=float(raw["taylor_exponent_n"]),
                    taylor_constant_c=float(raw["taylor_constant_c"]),
                    hardness_hrc=None,
                    data_source="database",
                    calibration_status="calibrated",
                )
                self._database_materials[params.id] = params
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("材料条目解析失败 %r: %s", raw.get("id", "?"), e)

        self._loaded = True
        logger.info(
            "材料数据库加载完成：%d 条（含 %d 条 HRC52 补充）",
            len(self._database_materials) + 1,
            1,
        )

    @staticmethod
    def _parse_range(raw: Any) -> dict[str, list[float]]:
        """解析 {roughing: [min,max], finishing: [min,max]} 结构。"""
        result: dict[str, list[float]] = {}
        for key in ("roughing", "finishing"):
            if key in raw:
                vals = raw[key]
                if isinstance(vals, list) and len(vals) == 2:
                    result[key] = [float(vals[0]), float(vals[1])]
                else:
                    result[key] = [0.0, 0.0]
        return result

    def get_material(self, material_id: str) -> MaterialParams:
        """按 ID 查询材料参数。

        优先级：HRC52 补充数据 > materials.json 数据库
        """
        self._ensure_loaded()

        if material_id == _HRC52_SUPPLEMENT.id:
            return _HRC52_SUPPLEMENT

        if material_id in self._database_materials:
            return self._database_materials[material_id]

        raise MaterialResolverError(f"材料 ID 未找到: {material_id}。可用材料：{self.list_material_ids()}")

    def list_material_ids(self) -> list[str]:
        """列出全部可用材料 ID（含 HRC52）。"""
        self._ensure_loaded()
        ids = list(self._database_materials.keys())
        ids.append(_HRC52_SUPPLEMENT.id)
        return sorted(ids)

    def list_materials(self) -> list[MaterialParams]:
        """列出全部材料参数。"""
        self._ensure_loaded()
        result = list(self._database_materials.values())
        result.append(_HRC52_SUPPLEMENT)
        return result

    def has_material(self, material_id: str) -> bool:
        """检查材料 ID 是否可用。"""
        self._ensure_loaded()
        if material_id == _HRC52_SUPPLEMENT.id:
            return True
        return material_id in self._database_materials


# 全局单例


_resolver_instance: MaterialResolver | None = None
_resolver_lock = threading.Lock()


def get_material_resolver() -> MaterialResolver:
    """获取全局 MaterialResolver 单例（双重检查锁）。"""
    global _resolver_instance
    if _resolver_instance is None:
        with _resolver_lock:
            if _resolver_instance is None:
                _resolver_instance = MaterialResolver()
    return _resolver_instance


def reset_material_resolver() -> None:
    """重置单例（供测试使用，避免测试间状态污染）。"""
    global _resolver_instance
    if _resolver_instance is not None:
        with _resolver_lock:
            if _resolver_instance is not None:
                _resolver_instance = None
