"""刀具磨损预测共享常量与查找函数。

从 ``app/services/tool_wear_predictor.py`` 抽取，供子服务共用。
"""

# 默认刀具更换阈值（磨损量占比），超过此值建议更换刀具
DEFAULT_REPLACEMENT_THRESHOLD = 0.3


class MaterialParams:
    def __init__(
        self,
        taylor_n: float,
        taylor_C: float,
        usui_A: float,
        usui_B: float,
        hardness_factor: float,
        name: str,
    ):
        self.taylor_n = taylor_n
        self.taylor_C = taylor_C
        self.usui_A = usui_A
        self.usui_B = usui_B
        self.hardness_factor = hardness_factor
        self.name = name


MATERIAL_PARAMS = {
    "aluminum_6061": MaterialParams(
        taylor_n=0.40,
        taylor_C=450.0,
        usui_A=0.002,
        usui_B=1200.0,
        hardness_factor=0.6,
        name="Aluminum 6061",
    ),
    "aluminum_7075": MaterialParams(
        taylor_n=0.35,
        taylor_C=380.0,
        usui_A=0.004,
        usui_B=1100.0,
        hardness_factor=0.75,
        name="Aluminum 7075",
    ),
    "steel_45": MaterialParams(
        taylor_n=0.25,
        taylor_C=280.0,
        usui_A=0.008,
        usui_B=900.0,
        hardness_factor=1.0,
        name="Steel 45#",
    ),
    "steel_4140": MaterialParams(
        taylor_n=0.23,
        taylor_C=250.0,
        usui_A=0.010,
        usui_B=850.0,
        hardness_factor=1.1,
        name="Steel 4140",
    ),
    "stainless_304": MaterialParams(
        taylor_n=0.20,
        taylor_C=200.0,
        usui_A=0.015,
        usui_B=800.0,
        hardness_factor=1.3,
        name="Stainless Steel 304",
    ),
    "stainless_316": MaterialParams(
        taylor_n=0.18,
        taylor_C=180.0,
        usui_A=0.018,
        usui_B=750.0,
        hardness_factor=1.4,
        name="Stainless Steel 316",
    ),
    "stainless_hrc52": MaterialParams(
        taylor_n=0.17,
        taylor_C=160.0,
        usui_A=0.020,
        usui_B=720.0,
        hardness_factor=1.6,
        name="Stainless Steel HRC52",
    ),
    "titanium_ti64": MaterialParams(
        taylor_n=0.15,
        taylor_C=120.0,
        usui_A=0.025,
        usui_B=650.0,
        hardness_factor=1.8,
        name="Titanium Ti-6Al-4V",
    ),
    "titanium_tc4": MaterialParams(
        taylor_n=0.14,
        taylor_C=110.0,
        usui_A=0.028,
        usui_B=620.0,
        hardness_factor=1.85,
        name="Titanium TC4 (Uniwear-NUAA)",
    ),
    "inconel_718": MaterialParams(
        taylor_n=0.12,
        taylor_C=90.0,
        usui_A=0.035,
        usui_B=600.0,
        hardness_factor=2.2,
        name="Inconel 718",
    ),
    "cast_iron": MaterialParams(
        taylor_n=0.22,
        taylor_C=220.0,
        usui_A=0.012,
        usui_B=850.0,
        hardness_factor=1.15,
        name="Cast Iron",
    ),
    "brass": MaterialParams(
        taylor_n=0.38,
        taylor_C=400.0,
        usui_A=0.003,
        usui_B=1150.0,
        hardness_factor=0.5,
        name="Brass",
    ),
    "default": MaterialParams(
        taylor_n=0.25,
        taylor_C=250.0,
        usui_A=0.010,
        usui_B=850.0,
        hardness_factor=1.0,
        name="Default",
    ),
}

TOOL_PARAMS = {
    "carbide": {"wear_factor": 1.0, "max_vb": 0.3},
    "coated_carbide": {"wear_factor": 0.7, "max_vb": 0.35},
    "cermet": {"wear_factor": 0.8, "max_vb": 0.3},
    "ceramic": {"wear_factor": 0.6, "max_vb": 0.35},
    "cbn": {"wear_factor": 0.4, "max_vb": 0.4},
    "pcd": {"wear_factor": 0.3, "max_vb": 0.35},
    "hss": {"wear_factor": 1.5, "max_vb": 0.25},
    "default": {"wear_factor": 1.0, "max_vb": 0.3},
}


def get_material_params(material_type: str) -> MaterialParams:
    """根据材料类型字符串模糊匹配 MaterialParams，未命中返回 default。"""
    mat_key = material_type.lower().replace(" ", "_").replace("-", "_")
    for key in MATERIAL_PARAMS:
        if key in mat_key or mat_key in key:
            return MATERIAL_PARAMS[key]
    return MATERIAL_PARAMS["default"]


def get_tool_params(tool_type: str) -> dict:
    """根据刀具类型字符串匹配 TOOL_PARAMS，未命中返回 default。"""
    tool_key = tool_type.lower().replace(" ", "_").replace("-", "_")
    # 精确匹配优先：避免 "carbide" 截胡 "coated_carbide" 等子串包含项
    if tool_key in TOOL_PARAMS:
        return TOOL_PARAMS[tool_key]
    for key in TOOL_PARAMS:
        if key in tool_key or tool_key in key:
            return TOOL_PARAMS[key]
    return TOOL_PARAMS["default"]
