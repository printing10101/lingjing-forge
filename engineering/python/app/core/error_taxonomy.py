"""面向制造场景的结构化错误分类体系。

将NC代码生成全流程（图纸解析→3D重建→工艺规划→刀轨计算）中
可能出现的错误按阶段分类，提供统一的结构化错误信息格式。

错误码规范：
- E1xxx: 图纸解析阶段
- E2xxx: 3D重建阶段
- E3xxx: 工艺规划阶段
- E4xxx: NC代码生成阶段
- E5xxx: 系统/基础设施错误
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(Enum):
    """制造场景错误分类枚举。"""

    # 图纸解析阶段 (E1xxx)
    DRAWING_PARSE_FAILED = (
        "E1001",
        "图纸解析失败",
        "critical",
        "图纸文件格式不支持或内容损坏。请确认文件格式为SVG/PNG/PDF，文件大小不超过10MB，图纸清晰度满足300dpi最低要求。",
    )
    LINE_RECOGNITION_FAILED = (
        "E1002",
        "线型识别失败",
        "error",
        "建议：检查图纸线型是否符合标准（ISO 128），实线/虚线/点划线应清晰可辨。"
        "可尝试使用更高对比度的图纸，或手动标注线型。",
    )
    DIMENSION_EXTRACTION_FAILED = (
        "E1003",
        "尺寸标注提取失败",
        "error",
        "建议：确认图纸包含完整的尺寸标注文字（非图形块），标注方向应水平或垂直对齐。复杂公差标注可能需要手动补充。",
    )
    FEATURE_RECOGNITION_INCOMPLETE = (
        "E1004",
        "特征识别不完整",
        "warning",
        "部分加工特征未能自动识别（如螺纹、花键、滚花等）。建议在特征管理界面手动添加缺失特征。",
    )
    VIEW_ALIGNMENT_FAILED = (
        "E1005",
        "三视图对齐失败",
        "error",
        "建议：确认三视图摆放位置符合第三角投影法规范，主视图/俯视图/侧视图对正关系正确。",
    )
    AMBIGUOUS_GEOMETRY = (
        "E1006",
        "几何信息歧义",
        "warning",
        "图纸中部分轮廓存在多种解释可能。建议补充剖面图或标注辅助尺寸以消除歧义。",
    )

    # 3D 重建阶段 (E2xxx)
    RECONSTRUCTION_FAILED = (
        "E2001",
        "3D重建失败",
        "critical",
        "重建算法无法从当前三视图生成有效3D模型。常见原因：视图信息不足、轮廓自相交、"
        "特征过于复杂。建议提供更清晰的三视图或补充辅助视图。",
    )
    GEOMETRY_INVALID = (
        "E2002",
        "重建几何体无效（非封闭/自相交）",
        "error",
        "生成的3D模型存在非封闭曲面或自相交几何体，无法用于后续加工。"
        "建议检查原始三视图中是否存在矛盾标注，或启用几何修复功能。",
    )
    PRECISION_BELOW_THRESHOLD = (
        "E2003",
        "重建精度低于阈值",
        "warning",
        "重建模型尺寸偏差超出可接受范围（>0.1mm）。建议使用更高分辨率的三视图（≥600dpi），或人工校核关键尺寸。",
    )
    TOPOLOGY_INCONSISTENT = (
        "E2004",
        "重建拓扑不一致",
        "error",
        "特征间相邻关系与标注信息不符。建议检查图纸中隐藏线、虚线表示的内部结构是否完整。",
    )
    MISSING_FEATURE_IN_RECONSTRUCTION = (
        "E2005",
        "重建模型缺失特征",
        "warning",
        "部分特征在重建过程中丢失（如倒角、圆角）。可在后续工艺规划中手动补充。",
    )

    # 工艺规划阶段 (E3xxx)
    PROCESS_PLANNING_FAILED = (
        "E3001",
        "工艺规划失败",
        "critical",
        "无法为当前零件生成有效加工工艺。请检查零件特征完整性和毛坯定义。",
    )
    NO_SUITABLE_TOOL = (
        "E3002",
        "刀具库中无合适刀具",
        "error",
        "建议：在刀具库中添加对应规格的刀具。例如：φ10硬质合金立铣刀、φ8球头铣刀。"
        "也可调整加工特征参数（如孔径）以匹配现有刀具。",
    )
    FIXTURE_CONFLICT = (
        "E3003",
        "装夹方案与加工特征冲突",
        "error",
        "当前装夹方案覆盖了待加工表面。建议更换定位基准面，或调整装夹位置。",
    )
    PARAMETER_OUT_OF_RANGE = (
        "E3004",
        "切削参数超出物理可行范围",
        "warning",
        "系统自动将参数调整至推荐范围内，请确认是否接受。",
    )
    CUTTING_FORCE_EXCEEDED = (
        "E3005",
        "切削力超过机床/刀具限制",
        "error",
        "建议：减小切深（ap）或进给量（f），或更换更大功率的机床。公式: Fc = kc × ap × f，调整任一参数均可降低切削力。",
    )
    POWER_EXCEEDED = (
        "E3006",
        "所需功率超过机床额定功率",
        "error",
        "建议：降低切削速度（Vc）或切削力（Fc）。公式: P = Fc × Vc / 60000。或选用额定功率更高的机床型号。",
    )
    SURFACE_ROUGHNESS_EXCEEDED = (
        "E3007",
        "表面粗糙度不满足要求",
        "warning",
        "当前参数计算的理论表面粗糙度超出精度要求。建议：减小进给量或增大刀尖圆弧半径。公式: Ra ≈ f² / (32 × rε)。",
    )
    TOOL_LIFE_INSUFFICIENT = (
        "E3008",
        "刀具寿命估算不足",
        "warning",
        "Taylor寿命公式估算的刀具寿命低于推荐值。建议适当降低切削速度以延长刀具寿命。公式: V × T^n = C。",
    )

    # NC 代码生成阶段 (E4xxx)
    TOOLPATH_GENERATION_FAILED = (
        "E4001",
        "刀轨生成失败",
        "critical",
        "刀具路径计算异常。常见原因：加工区域定义无效、刀具与特征不匹配。"
        "建议检查加工区域选择是否正确，刀具直径是否小于最小特征尺寸。",
    )
    POST_PROCESSOR_ERROR = (
        "E4002",
        "后处理转换失败",
        "error",
        "NC代码后处理器无法将刀轨转换为目标控制器格式。建议检查后处理器配置是否正确，控制器类型是否匹配。",
    )
    COLLISION_DETECTED = (
        "E4003",
        "检测到刀具碰撞风险",
        "error",
        "建议：调整安全高度或修改刀具路径，避免快速移动时切入毛坯。检查G00移动指令的Z轴高度是否大于毛坯顶面+安全余量。",
    )
    RAPID_MOVE_COLLISION = (
        "E4004",
        "快速移动路径存在碰撞",
        "critical",
        "G00快速移动路径穿过毛坯区域，存在严重撞刀风险。"
        "建议：在快速移动前先抬刀至安全平面（G00 Z[安全高度]），再执行水平定位。"
        "安全高度 = 毛坯最高点 + 10mm。",
    )
    TOOL_CHANGE_SAFETY_FAILED = (
        "E4005",
        "换刀点安全距离不足",
        "error",
        "换刀位置Z轴高度低于安全要求。建议在程序头设置G91 G28 Z0.回参考点后再换刀。",
    )

    # 系统/基础设施错误 (E5xxx)
    MODEL_NOT_LOADED = (
        "E5001",
        "AI模型未加载",
        "error",
        "LNN模型尚未完成加载。请等待模型预热完成（通常需要5-30秒），或检查模型文件路径是否正确。",
    )
    GPU_OUT_OF_MEMORY = (
        "E5002",
        "GPU显存不足",
        "error",
        "建议：减小批处理大小（batch_size），启用模型量化（-int8），"
        "或切换到CPU推理模式。当前可用GPU显存: 需通过 nvidia-smi 确认。",
    )
    INFERENCE_TIMEOUT = (
        "E5003",
        "推理超时",
        "warning",
        "模型推理时间超出预期（>30s）。建议：简化输入特征维度，或启用模型缓存减少重复计算。",
    )
    SERVICE_UNAVAILABLE_CAT = (
        "E5004",
        "服务暂不可用",
        "critical",
        "核心服务进程未响应。请检查服务状态，必要时重启服务。",
    )
    DATABASE_CONNECTION_FAILED = (
        "E5005",
        "数据库连接失败",
        "error",
        "无法连接到刀具/材料数据库。请检查数据库文件路径和读写权限。",
    )
    INTERNAL_ERROR = (
        "E5006",
        "系统内部错误",
        "critical",
        "系统发生未预期的内部错误。请联系技术支持并提供错误码以便排查。",
    )

    def __init__(
        self,
        code: str,
        message: str,
        severity: str,
        default_suggestion: str,
    ):
        self._code = code
        self._message = message
        self._severity = severity
        self._default_suggestion = default_suggestion

    @property
    def code(self) -> str:
        return self._code

    @property
    def message(self) -> str:
        return self._message

    @property
    def severity(self) -> str:
        return self._severity

    @property
    def default_suggestion(self) -> str:
        return self._default_suggestion

    @property
    def is_critical(self) -> bool:
        return self._severity == "critical"

    @property
    def is_error(self) -> bool:
        return self._severity in ("critical", "error")

    @property
    def is_warning(self) -> bool:
        return self._severity == "warning"

    @classmethod
    def from_code(cls, code: str) -> ErrorCategory | None:
        for cat in cls:
            if cat.code == code:
                return cat
        return None

    @classmethod
    def list_by_stage(cls, stage: str) -> list[ErrorCategory]:
        """按加工阶段筛选错误类型。"""
        prefix_map = {
            "drawing": "E1",
            "reconstruction": "E2",
            "process": "E3",
            "toolpath": "E4",
            "system": "E5",
        }
        prefix = prefix_map.get(stage, "")
        return [c for c in cls if c.code.startswith(prefix)]

    @classmethod
    def list_by_severity(cls, severity: str) -> list[ErrorCategory]:
        return [c for c in cls if c.severity == severity]


class ManufacturingError(Exception):
    """制造场景统一异常类。

    对所有制造流程中的错误提供结构化信息，
    包含错误码、严重程度、详细描述、修复建议和可恢复标识。

    Attributes:
        code: 错误码（如 E3004）
        message: 错误描述
        severity: 严重程度（critical/error/warning）
        detail: 详细错误上下文
        suggestion: 修复建议
        recoverable: 是否可自动恢复
        adjusted_values: 调整后的参数值（如有）
    """

    def __init__(
        self,
        category: ErrorCategory,
        detail: str = "",
        suggestion: str | None = None,
        recoverable: bool = False,
        adjusted_values: dict[str, Any] | None = None,
    ):
        self.code = category.code
        self.message = category.message
        self.severity = category.severity
        self.detail = detail
        self.suggestion = suggestion or category.default_suggestion
        self.recoverable = recoverable
        self.adjusted_values = adjusted_values or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """转换为API响应格式的结构化字典。"""
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "recoverable": self.recoverable,
        }
        if self.adjusted_values:
            result["adjusted_values"] = self.adjusted_values
        return result

    def to_response(self) -> dict[str, Any]:
        """构建标准API错误响应。"""
        from app.core.request_id import get_request_id

        return {
            **self.to_dict(),
            "request_id": get_request_id(),
        }

    @classmethod
    def from_code(
        cls,
        code: str,
        detail: str = "",
        suggestion: str | None = None,
        recoverable: bool = False,
        adjusted_values: dict[str, Any] | None = None,
    ) -> ManufacturingError:
        """通过错误码字符串快速创建异常。"""
        category = ErrorCategory.from_code(code)
        if category is None:
            category = ErrorCategory.INTERNAL_ERROR
        return cls(
            category=category,
            detail=detail,
            suggestion=suggestion,
            recoverable=recoverable,
            adjusted_values=adjusted_values,
        )


# 错误码映射表（与现有 ErrorCode 兼容）

CATEGORY_TO_NUMERIC: dict[str, int] = {
    # E1xxx 映射到 1001-1099
    "E1001": 1001,
    "E1002": 1002,
    "E1003": 1003,
    "E1004": 1004,
    "E1005": 1005,
    "E1006": 1006,
    # E2xxx 映射到 2001-2099
    "E2001": 2001,
    "E2002": 2002,
    "E2003": 2003,
    "E2004": 2004,
    "E2005": 2005,
    # E3xxx 映射到 3001-3099
    "E3001": 3001,
    "E3002": 3002,
    "E3003": 3003,
    "E3004": 3004,
    "E3005": 3005,
    "E3006": 3006,
    "E3007": 3007,
    "E3008": 3008,
    # E4xxx 映射到 4001-4099
    "E4001": 4001,
    "E4002": 4002,
    "E4003": 4003,
    "E4004": 4004,
    "E4005": 4005,
    # E5xxx 映射到 5001-5099
    "E5001": 5001,
    "E5002": 5002,
    "E5003": 5003,
    "E5004": 5004,
    "E5005": 5005,
    "E5006": 5006,
}


def category_to_numeric(category: ErrorCategory) -> int:
    """将错误分类转换为数值错误码（与现有response模块兼容）。"""
    return CATEGORY_TO_NUMERIC.get(category.code, 9001)


def manufacturing_error_response(
    error: ManufacturingError,
) -> dict[str, Any]:
    """将ManufacturingError转换为标准API响应格式。

    融合现有的code（数值）与新结构化字段。
    """
    from app.core.request_id import get_request_id

    response: dict[str, Any] = {
        "code": int(error.code[1:]),  # E3004 -> 3004
        "error_code": error.code,  # E3004
        "message": error.message,
        "severity": error.severity,
        "request_id": get_request_id(),
    }
    if error.detail:
        response["detail"] = error.detail
    if error.suggestion:
        response["suggestion"] = error.suggestion
    if error.recoverable:
        response["recoverable"] = True
    if error.adjusted_values:
        response["adjusted_values"] = error.adjusted_values
    return response
