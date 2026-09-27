"""机械加工工艺规划系统基础数据层。

提供JSON知识库数据的加载、验证、查询和管理功能。
包含材料库、刀具库、切削参数库和工艺规则库的完整数据访问接口。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MaterialEntry:
    id: str
    name: str
    category: str
    density_gcm3: float
    hardness_hb: float
    tensile_strength_mpa: float
    cutting_performance: str
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaterialEntry:
        # 2026-09 schema 适配：新版 materials.json（物理参数体系）没有
        # cutting_performance 字段，此前 17 条材料全部因缺该键被
        # "跳过不兼容"清空（知识库冷启动缺失）。该字段仅作描述性消费
        # （知识图谱/SHARP 匹配），规划数学不依赖——缺省时按比切削力
        # 推导粗粒度等级。
        cutting_performance = data.get("cutting_performance")
        if not cutting_performance:
            kcs = data.get("specific_cutting_force")
            if isinstance(kcs, (int, float)) and kcs > 0:
                cutting_performance = (
                    "excellent" if kcs <= 1500 else "good" if kcs <= 2000 else "fair" if kcs <= 2500 else "poor"
                )
            else:
                cutting_performance = "standard"
        return cls(
            id=data["id"],
            name=data["name"],
            category=data["category"],
            density_gcm3=data["density_gcm3"],
            hardness_hb=data["hardness_hb"],
            tensile_strength_mpa=data["tensile_strength_mpa"],
            cutting_performance=cutting_performance,
            description=data.get("description", ""),
        )


@dataclass
class ToolEntry:
    id: str
    series: str
    name: str
    diameter_mm: float
    material: str
    application: str
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolEntry:
        return cls(
            id=data["id"],
            series=data["series"],
            name=data["name"],
            diameter_mm=data["diameter_mm"],
            material=data["material"],
            application=data["application"],
            description=data.get("description", ""),
        )


@dataclass
class CuttingParameterEntry:
    id: str
    material_id: str
    material_name: str
    tool_series: str
    tool_material: str
    cutting_speed_min_mpm: float
    cutting_speed_max_mpm: float
    feed_min_mmpr: float
    feed_max_mmpr: float
    feed_unit: str
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CuttingParameterEntry:
        return cls(
            id=data["id"],
            material_id=data["material_id"],
            material_name=data["material_name"],
            tool_series=data["tool_series"],
            tool_material=data["tool_material"],
            cutting_speed_min_mpm=data["cutting_speed_min_mpm"],
            cutting_speed_max_mpm=data["cutting_speed_max_mpm"],
            feed_min_mmpr=data["feed_min_mmpr"],
            feed_max_mmpr=data["feed_max_mmpr"],
            feed_unit=data["feed_unit"],
            description=data.get("description", ""),
        )


@dataclass
class ProcessRuleEntry:
    id: str
    name: str
    category: str
    description: str
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessRuleEntry:
        return cls(
            id=data["id"],
            name=data["name"],
            category=data["category"],
            description=data["description"],
            details=data.get("details", {}),
        )


class DataValidationError(Exception):
    """数据验证异常"""

    pass


class DataLoadError(Exception):
    """数据加载异常"""

    pass


class QueryError(Exception):
    """查询异常"""

    pass


class ProcessPlanningDataManager:
    """工艺规划数据管理器。

    提供统一的数据访问接口，支持JSON数据加载、验证和查询。
    """

    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent
        self._data_dir = Path(data_dir)
        # 回退数据目录：materials.json / tools.json 位于 app/database/data/
        # （与 material_resolver 的默认路径一致），cutting_parameters.json /
        # process_rules.json 位于本模块同目录。多目录查找避免数据文件漂移。
        self._fallback_dir = self._data_dir.parent / "database" / "data"
        self._materials: dict[str, MaterialEntry] = {}
        self._tools: dict[str, ToolEntry] = {}
        self._cutting_parameters: dict[str, CuttingParameterEntry] = {}
        self._process_rules: dict[str, ProcessRuleEntry] = {}
        self._load_all()

    def _load_json(self, filename: str) -> list[dict[str, Any]]:
        filepath = self._data_dir / filename
        if not filepath.exists():
            fallback = self._fallback_dir / filename
            if fallback.exists():
                filepath = fallback
            else:
                raise DataLoadError(f"数据文件不存在: {filepath}（回退目录 {self._fallback_dir} 亦未找到）")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise DataValidationError(f"数据文件格式错误: {filename} 应为数组格式")
            return data
        except json.JSONDecodeError as e:
            raise DataLoadError(f"JSON解析失败: {filename}, 错误: {e}") from e
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.error("加载数据文件失败: %s, 错误: %s", filename, e)
            raise DataLoadError(f"加载数据文件失败: {filename}, 错误: {e}") from e

    def _load_all(self) -> None:
        self._load_materials()
        self._load_tools()
        self._load_cutting_parameters()
        self._load_process_rules()

    def _load_materials(self) -> None:
        raw_data = self._load_json_optional("materials.json")
        if not raw_data:
            return
        for item in raw_data:
            try:
                entry = MaterialEntry.from_dict(item)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("跳过不兼容的材料条目 %s: %s", item.get("id", "?"), e)
                continue
            self._materials[entry.id] = entry

    def _load_tools(self) -> None:
        """加载刀具库。

        与材料一致，刀具数据统一存放于 ``app/database/data/tools.json``（新版格式），
        由 ``app.database.tools.ToolDatabase`` 消费。此处直接委派新版数据库层读取，
        并将新版 ``type``/``subtype`` 映射为旧 ``ToolEntry.series`` / ``diameter_mm`` /
        ``application`` 形态，避免旧解析器因缺 ``series`` 等字段将所有刀具跳过。
        """
        try:
            from app.database.tools import ToolDatabase
        except ImportError:
            self._load_tools_from_raw(self._load_json_optional("tools.json"))
            return
        try:
            new_tools = ToolDatabase().list_all()
        except Exception as e:  # pragma: no cover - 兜底
            logger.warning("新版刀具库加载失败，回退 JSON 解析: %s", e)
            self._load_tools_from_raw(self._load_json_optional("tools.json"))
            return
        seen: set[str] = set()
        for t in new_tools:
            series = self._tool_series_for_type(t.type)
            diameter = self._tool_nominal_diameter(t.diameter_range)
            # 同名/同系列且同直径的刀具去重，避免重复条目干扰精确匹配
            key = (series, round(diameter, 3))
            if key in seen:
                continue
            seen.add(key)
            entry = ToolEntry(
                id=t.id,
                series=series,
                name=t.name,
                diameter_mm=diameter,
                material=t.material,
                application=self._tool_application_for_type(t.type),
                description=f"type={t.type}, subtype={t.subtype}",
            )
            self._tools[entry.id] = entry

    def _load_tools_from_raw(self, raw_data: list[dict[str, Any]]) -> None:
        for item in raw_data:
            try:
                entry = ToolEntry.from_dict(item)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("跳过不兼容的刀具条目 %s: %s", item.get("id", "?"), e)
                continue
            self._tools[entry.id] = entry

    @staticmethod
    def _tool_series_for_type(tool_type: str) -> str:
        """新版刀具 type → 旧 series 映射（与 process_map 对齐）。"""
        return {
            "endmill": "endmill",
            "drill": "twist_drill",
            "facemill": "face_mill",
            "center_drill": "center_drill",
            "tap": "tap",
            "turning": "turning",
            "chamfer": "chamfer",
            "engraving": "endmill",
        }.get(tool_type, tool_type)

    @staticmethod
    def _tool_nominal_diameter(diameter_range: list[float]) -> float:
        """按直径区间取标称直径：优先上限，其次区间中点。"""
        if not diameter_range:
            return 0.0
        upper = diameter_range[-1]
        lower = diameter_range[0]
        if upper and lower:
            return round((lower + upper) / 2.0, 3)
        return float(upper or lower or 0.0)

    @staticmethod
    def _tool_application_for_type(tool_type: str) -> str:
        return {
            "endmill": "型腔/轮廓加工",
            "drill": "钻孔",
            "facemill": "平面加工",
            "center_drill": "打中心孔定位",
            "tap": "攻丝",
            "turning": "车削",
            "chamfer": "倒角",
        }.get(tool_type, "通用加工")

    def _load_json_optional(self, filename: str) -> list[dict[str, Any]]:
        """容错加载：文件缺失或格式不兼容时记录 warning 并返回空列表。

        材料/刀具数据已迁移至 ``app/database/data/``（新格式，由
        ``material_resolver`` 消费），本模块的旧格式 Entry 无法解析时
        不应使整个数据管理器构造失败——ToolParamMatcher 对空数据有
        通用 fallback 设计。
        """
        try:
            return self._load_json(filename)
        except (DataLoadError, DataValidationError) as e:
            logger.warning("工艺规划数据加载跳过 %s: %s", filename, e)
            return []

    def _load_cutting_parameters(self) -> None:
        raw_data = self._load_json("cutting_parameters.json")
        for item in raw_data:
            entry = CuttingParameterEntry.from_dict(item)
            self._cutting_parameters[entry.id] = entry

    def _load_process_rules(self) -> None:
        raw_data = self._load_json("process_rules.json")
        for item in raw_data:
            entry = ProcessRuleEntry.from_dict(item)
            self._process_rules[entry.id] = entry

    # 常见英文/泛化材料名 → 知识库材料 id（键为 _normalize_material_name
    # 形态）。nl2cad 等对外接口的默认值是英文名（如 "steel"），知识库
    # 规范名是中文（如 "45钢"），不做映射则默认参数必然查询失败。映射取
    # 机械加工惯例：泛化 "steel" 对应最常用的 45 号碳素结构钢。
    _MATERIAL_ALIASES: dict[str, str] = {
        "steel": "steel_45",
        "carbonsteel": "steel_45",
        "45steel": "steel_45",
        "alloysteel": "steel_40cr",
        "stainlesssteel": "ss_304",
        "aluminum": "al_6061",
        "aluminium": "al_6061",
        "titanium": "ti_tc4",
        "grayiron": "cast_iron_ht250",
        "castiron": "cast_iron_ht250",
        "brass": "brass_c36000",
        "copper": "copper_c11000",
    }

    @classmethod
    def _normalize_material_name(cls, name: str) -> str:
        """归一化材料名：去井号与空白。

        知识库材料名为"45钢"形态，用户输入与各调用方默认值常见"45#钢"
        （与 agent/orchestrator.py 的入口归一化规则一致）。归一化放在
        查询层使所有调用路径（dxf pipeline / gcode / cam_validation /
        agent）统一命中。
        """
        return re.sub(r"[#\s_]", "", name).lower()

    def get_material_by_name(self, name: str) -> MaterialEntry | None:
        """按材料名称查询材料属性。

        Args:
            name: 材料名称，支持模糊匹配（忽略井号与空白差异，如
                "45#钢" 与 "45钢" 等价；常见英文名如 "steel" 映射到
                知识库规范材料）

        Returns:
            MaterialEntry: 匹配的材料条目，未找到返回None

        Raises:
            QueryError: 当输入参数无效时
        """
        if not name or not name.strip():
            raise QueryError("材料名称不能为空")
        search_name = self._normalize_material_name(name)
        for material in self._materials.values():
            material_name = self._normalize_material_name(material.name)
            if material_name == search_name or search_name in material_name:
                return material
        # 精确/子串未命中时尝试英文别名表
        alias = self._MATERIAL_ALIASES.get(search_name)
        if alias:
            return self._materials.get(alias)
        return None

    def get_tools_by_material_and_process(self, material_category: str, process: str) -> list[ToolEntry]:
        """按材料类型和加工工序查询适用刀具。

        Args:
            material_category: 材料类型，如 carbon_steel, aluminum 等
            process: 加工工序，如 钻孔, 型腔/轮廓加工, 平面加工 等

        Returns:
            list[ToolEntry]: 适用的刀具列表
        """
        if not material_category or not material_category.strip():
            raise QueryError("材料类型不能为空")
        if not process or not process.strip():
            raise QueryError("加工工序不能为空")

        process_map = {
            "钻孔": "twist_drill",
            "型腔/轮廓加工": "endmill",
            "平面加工": "face_mill",
            "打中心孔定位": "center_drill",
        }
        tool_series = process_map.get(process.strip())
        if not tool_series:
            return []

        return [tool for tool in self._tools.values() if tool.series == tool_series]

    def get_cutting_parameters(self, material_id: str, tool_series: str) -> list[CuttingParameterEntry]:
        """按材料类型和刀具类型查询推荐切削参数。

        Args:
            material_id: 材料ID
            tool_series: 刀具系列，如 twist_drill, endmill 等

        Returns:
            list[CuttingParameterEntry]: 切削参数列表
        """
        if not material_id or not material_id.strip():
            raise QueryError("材料ID不能为空")
        if not tool_series or not tool_series.strip():
            raise QueryError("刀具系列不能为空")

        results = []
        for param in self._cutting_parameters.values():
            if param.material_id == material_id.strip() and param.tool_series == tool_series.strip():
                results.append(param)
        return results

    def get_material_by_id(self, material_id: str) -> MaterialEntry | None:
        """按ID查询材料。

        Args:
            material_id: 材料ID

        Returns:
            MaterialEntry: 材料条目，未找到返回None
        """
        return self._materials.get(material_id)

    def get_tool_by_id(self, tool_id: str) -> ToolEntry | None:
        """按ID查询刀具。

        Args:
            tool_id: 刀具ID

        Returns:
            ToolEntry: 刀具条目，未找到返回None
        """
        return self._tools.get(tool_id)

    def get_all_materials(self) -> list[MaterialEntry]:
        """获取所有材料。

        Returns:
            list[MaterialEntry]: 材料列表
        """
        return list(self._materials.values())

    def get_all_tools(self) -> list[ToolEntry]:
        """获取所有刀具。

        Returns:
            list[ToolEntry]: 刀具列表
        """
        return list(self._tools.values())

    def get_all_cutting_parameters(self) -> list[CuttingParameterEntry]:
        """获取所有切削参数。

        Returns:
            list[CuttingParameterEntry]: 切削参数列表
        """
        return list(self._cutting_parameters.values())

    def get_all_process_rules(self) -> list[ProcessRuleEntry]:
        """获取所有工艺规则。

        Returns:
            list[ProcessRuleEntry]: 工艺规则列表
        """
        return list(self._process_rules.values())

    def get_process_rule_by_id(self, rule_id: str) -> ProcessRuleEntry | None:
        """按ID查询工艺规则。

        Args:
            rule_id: 规则ID

        Returns:
            ProcessRuleEntry: 规则条目，未找到返回None
        """
        return self._process_rules.get(rule_id)

    def get_process_rules_by_category(self, category: str) -> list[ProcessRuleEntry]:
        """按类别查询工艺规则。

        Args:
            category: 规则类别

        Returns:
            list[ProcessRuleEntry]: 规则列表
        """
        return [rule for rule in self._process_rules.values() if rule.category == category]

    def get_tools_by_series(self, series: str) -> list[ToolEntry]:
        """按刀具系列查询刀具。

        Args:
            series: 刀具系列

        Returns:
            list[ToolEntry]: 刀具列表
        """
        return [tool for tool in self._tools.values() if tool.series == series]

    def get_materials_by_category(self, category: str) -> list[MaterialEntry]:
        """按材料类别查询材料。

        Args:
            category: 材料类别

        Returns:
            list[MaterialEntry]: 材料列表
        """
        return [material for material in self._materials.values() if material.category == category]

    def validate_data_integrity(self) -> dict[str, Any]:
        """验证数据完整性。

        Returns:
            dict: 验证结果，包含统计信息和错误列表
        """
        errors = []
        stats = {
            "materials_count": len(self._materials),
            "tools_count": len(self._tools),
            "cutting_parameters_count": len(self._cutting_parameters),
            "process_rules_count": len(self._process_rules),
        }

        for param in self._cutting_parameters.values():
            if param.material_id not in self._materials:
                errors.append(f"切削参数 {param.id} 引用了不存在的材料: {param.material_id}")

        return {
            "stats": stats,
            "errors": errors,
            "is_valid": len(errors) == 0,
        }

    def __repr__(self) -> str:
        return (
            f"ProcessPlanningDataManager("
            f"materials={len(self._materials)}, "
            f"tools={len(self._tools)}, "
            f"cutting_params={len(self._cutting_parameters)}, "
            f"rules={len(self._process_rules)})"
        )
