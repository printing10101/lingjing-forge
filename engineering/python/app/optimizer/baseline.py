"""L0 规则基线参数库（纯白盒，零框架依赖）。

材料 × 加工类型 → 推荐切削参数的经验表。数据来源：
- 机械加工手册经验值（ISO 材料组 + 常用刀具材料）
- 可被 L1 统计推荐覆盖：当 cutting_experience 积累足够数据后，
  统计均值优先于经验表

设计要点：
- 纯 dataclass + dict，无数据库依赖，可独立测试
- 所有推荐参数带物理安全区间（clamp 用）
- lookup 支持模糊匹配（大小写不敏感、材料名包含匹配）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BaselineEntry:
    """一条材料×加工类型的基础推荐参数。"""

    material: str
    machining_type: str  # milling/turning/drilling/tapping/boring
    tool_material: str = "carbide"  # carbide/hss/coated
    depth_of_cut_mm: float = 1.0
    feed_mm_per_rev: float = 0.15
    spindle_rpm: float = 6000.0
    cutting_speed_m_min: float = 150.0

    # 安全区间（用于 clamp）
    depth_min: float = 0.1
    depth_max: float = 5.0
    feed_min: float = 0.02
    feed_max: float = 0.8
    rpm_min: float = 100.0
    rpm_max: float = 24000.0


def _b(
    material: str,
    mtype: str,
    depth: float,
    feed: float,
    rpm: float,
    speed: float,
    tool: str = "carbide",
) -> BaselineEntry:
    """构造条目的简写工厂函数。"""
    return BaselineEntry(
        material=material,
        machining_type=mtype,
        tool_material=tool,
        depth_of_cut_mm=depth,
        feed_mm_per_rev=feed,
        spindle_rpm=rpm,
        cutting_speed_m_min=speed,
    )


# 默认基线：覆盖常见材料 × 加工类型组合
# 切削速度参考 ISO 材料组经验值（HSS/硬质合金）
DEFAULT_BASELINE: tuple[BaselineEntry, ...] = (
    # --- 铝合金（AL6061 等）---
    _b("AL6061", "milling", 2.0, 0.2, 8000, 300),
    _b("AL6061", "turning", 1.5, 0.2, 3000, 300),
    _b("AL6061", "drilling", 1.0, 0.15, 6000, 200),
    _b("AL6061", "tapping", 1.0, 0.1, 1500, 120),
    # --- 不锈钢（SS304 等）---
    _b("SS304", "milling", 1.0, 0.12, 4000, 120),
    _b("SS304", "turning", 1.0, 0.15, 1500, 120),
    _b("SS304", "drilling", 0.8, 0.1, 2000, 80),
    _b("SS304", "tapping", 0.8, 0.08, 600, 60),
    # 45# 钢 / 碳钢
    _b("45", "milling", 1.5, 0.15, 5000, 160),
    _b("45", "turning", 1.5, 0.18, 2000, 160),
    _b("45", "drilling", 1.0, 0.12, 3000, 100),
    # --- 钛合金（Ti-6Al-4V 等）---
    _b("Ti6Al4V", "milling", 0.5, 0.08, 3000, 60),
    _b("Ti6Al4V", "turning", 0.5, 0.1, 1000, 60),
    _b("Ti6Al4V", "drilling", 0.4, 0.06, 800, 30),
    # --- 铸铁（HT250 等）---
    _b("HT250", "milling", 2.0, 0.18, 4000, 140),
    _b("HT250", "turning", 2.0, 0.2, 1500, 140),
    _b("HT250", "drilling", 1.2, 0.15, 2500, 90),
)

# 常见别名 规范材料名（模糊匹配用）
_MATERIAL_ALIASES: dict[str, str] = {
    "al6061": "AL6061",
    "6061": "AL6061",
    "aluminum": "AL6061",
    "铝合金": "AL6061",
    "ss304": "SS304",
    "304": "SS304",
    "stainless": "SS304",
    "不锈钢": "SS304",
    "carbon steel": "45",
    "45#": "45",
    "steel": "45",
    "碳钢": "45",
    "ti6al4v": "Ti6Al4V",
    "titanium": "Ti6Al4V",
    "钛合金": "Ti6Al4V",
    "ht250": "HT250",
    "cast iron": "HT250",
    "铸铁": "HT250",
}


@dataclass
class BaselineLibrary:
    """基线参数库（可扩展/可覆盖）。"""

    entries: list[BaselineEntry] = field(default_factory=lambda: list(DEFAULT_BASELINE))

    def lookup(self, material: str, machining_type: str) -> BaselineEntry | None:
        """按材料+加工类型精确查找（大小写不敏感 + 别名归一）。"""
        norm_material = _normalize_material(material)
        for entry in self.entries:
            if (
                _normalize_material(entry.material) == norm_material
                and entry.machining_type.lower() == machining_type.lower()
            ):
                return entry
        return None

    def add(self, entry: BaselineEntry) -> None:
        """新增/覆盖一条基线（飞轮学习到更优参数后调用）。"""
        # 覆盖同键旧条目
        self.entries = [
            e
            for e in self.entries
            if not (
                _normalize_material(e.material) == _normalize_material(entry.material)
                and e.machining_type == entry.machining_type
                and e.tool_material == entry.tool_material
            )
        ]
        self.entries.append(entry)


def _normalize_material(material: str) -> str:
    """材料名归一化：去空白 + 小写 + 别名映射。"""
    raw = material.strip().lower()
    return _MATERIAL_ALIASES.get(raw, raw)


def lookup_baseline(
    material: str,
    machining_type: str,
    library: BaselineLibrary | None = None,
) -> BaselineEntry | None:
    """便捷函数：从默认库或指定库查找基线。"""
    lib = library or BaselineLibrary()
    return lib.lookup(material, machining_type)


__all__ = [
    "BaselineEntry",
    "BaselineLibrary",
    "DEFAULT_BASELINE",
    "lookup_baseline",
    "_normalize_material",
]
