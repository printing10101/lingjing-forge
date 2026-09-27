"""Input validation utilities for API endpoints."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from collections.abc import Sequence

# 允许的根目录白名单（防止路径遍历攻击）
# 调用方可通过环境变量 LNN_ALLOWED_ROOTS（逗号分隔）扩展；
# 默认包含 LNN_DATA_DIR / LNN_OUTPUT_DIR / LNN_UPLOAD_DIR 对应的 data/output/uploads
# 目录。环境变量在每次调用时动态读取，方便测试时按需调整。
_DEFAULT_ROOT_NAMES: tuple[str, str, str] = ("LNN_DATA_DIR", "LNN_OUTPUT_DIR", "LNN_UPLOAD_DIR")
_DEFAULT_FALLBACK_DIRS: tuple[str, ...] = ("data", "output", "uploads")

# P2-5-4 修复：提取校验阈值魔法数字为命名常量，便于统一管理与调整
# 切削参数上限（单位见各注释）
MAX_CUTTING_SPEED: float = 10_000.0  # m/min
MAX_FEED_RATE: float = 50.0  # mm/r
MAX_DEPTH_OF_CUT: float = 100.0  # mm
# 预测时长上限：24 小时 = 86400 秒
MAX_PREDICTION_HORIZON_SECONDS: float = 86_400.0
# 模型名称长度上限
MAX_MODEL_NAME_LENGTH: int = 64
# 训练参数上限
MAX_EPOCHS: int = 10_000
MAX_BATCH_SIZE: int = 512
MAX_LEARNING_RATE: float = 1.0
MAX_VALIDATION_SPLIT: float = 0.5
# 字符串长度上限
MAX_SANITIZE_LENGTH: int = 256
MAX_RAG_QUERY_LENGTH: int = 2000
MAX_MATERIAL_NAME_LENGTH: int = 100
MAX_GENERIC_TEXT_LENGTH: int = 4000


def _resolve_allowed_roots(
    extra_roots: Sequence[str] | None = None,
) -> list[Path]:
    """合并默认与调用方提供的允许根目录。

    注意：环境变量在每次调用时读取，避免模块级 import-time 副作用
    影响测试场景（pytest 通过 monkeypatch.setenv 注入临时目录）。
    """
    roots: list[Path] = []
    for env_name in _DEFAULT_ROOT_NAMES:
        env_value = os.environ.get(env_name)
        if env_value:
            roots.append(Path(env_value).resolve())
    # 如果 env 中未设置任何白名单目录，则退回到 CWD 下的默认子目录
    if not roots:
        cwd = Path(os.getcwd()).resolve()
        for sub in _DEFAULT_FALLBACK_DIRS:
            roots.append(cwd / sub)
    if extra_roots:
        roots.extend(Path(r).resolve() for r in extra_roots)
    return roots


def _is_within_allowed_roots(resolved: Path, allowed_roots: Sequence[Path]) -> bool:
    """检查解析后的路径是否落在白名单根目录内。"""
    if not allowed_roots:
        return False
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def validate_cutting_parameters(
    cutting_speed: float,
    feed_rate: float,
    depth_of_cut: float,
) -> list[str]:
    errors = []
    if cutting_speed <= 0:
        errors.append("切削速度必须大于0")
    elif cutting_speed > MAX_CUTTING_SPEED:
        errors.append(f"切削速度不能超过{int(MAX_CUTTING_SPEED)} m/min")
    if feed_rate <= 0:
        errors.append("进给量必须大于0")
    elif feed_rate > MAX_FEED_RATE:
        errors.append(f"进给量不能超过{int(MAX_FEED_RATE)} mm/r")
    if depth_of_cut <= 0:
        errors.append("切削深度必须大于0")
    elif depth_of_cut > MAX_DEPTH_OF_CUT:
        errors.append(f"切削深度不能超过{int(MAX_DEPTH_OF_CUT)} mm")
    return errors


def validate_prediction_horizon(horizon: float) -> list[str]:
    errors = []
    if horizon <= 0:
        errors.append("预测时长必须大于0")
    elif horizon > MAX_PREDICTION_HORIZON_SECONDS:
        errors.append("预测时长不能超过24小时")
    return errors


def validate_training_params(
    model_name: str,
    dataset_path: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    validation_split: float,
    model_type: str = "CFC",
) -> list[str]:
    errors = []
    if not model_name or not model_name.strip():
        errors.append("模型名称不能为空")
    elif not re.match(rf"^[a-zA-Z0-9_\-]{{1,{MAX_MODEL_NAME_LENGTH}}}$", model_name):
        errors.append(f"模型名称只能包含字母、数字、下划线和连字符，长度1-{MAX_MODEL_NAME_LENGTH}")
    if not dataset_path:
        errors.append("数据集路径不能为空")
    elif not os.path.exists(dataset_path):
        errors.append(f"数据集路径不存在: {dataset_path}")
    if epochs <= 0 or epochs > MAX_EPOCHS:
        errors.append(f"训练轮数必须在1-{MAX_EPOCHS}之间")
    if batch_size <= 0 or batch_size > MAX_BATCH_SIZE:
        errors.append(f"批量大小必须在1-{MAX_BATCH_SIZE}之间")
    if learning_rate <= 0 or learning_rate > MAX_LEARNING_RATE:
        errors.append("学习率必须在0-1之间")
    if not 0.0 <= validation_split <= MAX_VALIDATION_SPLIT:
        errors.append(f"验证集比例必须在0.0-{MAX_VALIDATION_SPLIT}之间")
    valid_types = {"CFC", "LTC", "HYBRID", "cnn", "CNN"}
    if model_type.upper() not in valid_types:
        errors.append(f"不支持的模型类型: {model_type}")
    return errors


def sanitize_string(value: str, max_length: int = MAX_SANITIZE_LENGTH) -> str:
    return value.strip()[:max_length]


def validate_rag_query(query: str) -> list[str]:
    errors = []
    if not query or not query.strip():
        errors.append("查询文本不能为空")
    elif len(query) > MAX_RAG_QUERY_LENGTH:
        errors.append(f"查询文本不能超过{MAX_RAG_QUERY_LENGTH}个字符")
    return errors


def validate_material_name(material: str) -> list[str]:
    errors = []
    if not material or not material.strip():
        errors.append("材料名称不能为空")
    elif len(material) > MAX_MATERIAL_NAME_LENGTH:
        errors.append(f"材料名称不能超过{MAX_MATERIAL_NAME_LENGTH}个字符")
    return errors


def validate_file_path(
    path: str,
    must_exist: bool = True,
    allowed_roots: Sequence[str] | None = None,
) -> list[str]:
    """校验文件路径，阻止路径遍历攻击。

    解析为绝对路径后，必须落在允许的根目录白名单内（含默认 data/output/uploads
    及调用方提供的扩展目录）。空路径、不存在路径（当 must_exist=True）、
    指向白名单外的路径均会被拒绝。
    """
    errors: list[str] = []
    if not path:
        errors.append("文件路径不能为空")
        return errors
    if not isinstance(path, str):
        errors.append("文件路径类型不合法")
        return errors

    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        # ValueError：Linux 下 Path.resolve 对含 NUL 字节的路径抛
        # "embedded null character"，必须当作非法路径拒绝而非向上抛 500
        errors.append(f"文件路径无法解析: {exc}")
        return errors

    roots = _resolve_allowed_roots(allowed_roots)
    if not _is_within_allowed_roots(resolved, roots):
        errors.append("文件路径不在允许的访问范围内")
        return errors

    if must_exist and not resolved.exists():
        errors.append(f"文件路径不存在: {path}")
    return errors


def coalesce(*values: Any) -> Any:
    """Return the first non-None value from the given arguments."""
    for v in values:
        if v is not None:
            return v
    return None


# 类校验器与结构化错误：用于 agents.py 等需要返回详细错误对象的场景

# 常见公差等级（IT01 ~ IT18）
_TOLERANCE_GRADES: tuple[str, ...] = (
    "IT01",
    "IT0",
    "IT1",
    "IT2",
    "IT3",
    "IT4",
    "IT5",
    "IT6",
    "IT7",
    "IT8",
    "IT9",
    "IT10",
    "IT11",
    "IT12",
    "IT13",
    "IT14",
    "IT15",
    "IT16",
    "IT17",
    "IT18",
)

# 允许的尺寸字段与上限
_SIZE_FIELDS: tuple[str, ...] = ("length", "width", "height", "diameter")
_SIZE_MAX_VALUE: float = 100_000.0  # 单位 mm

# 允许的材料黑名单字符（防止注入或控制字符）
_FORBIDDEN_MATERIAL_CHARS = re.compile(r"[\x00-\x1f\x7f<>\"'`;{}\\]")


class ValidationErrorDetail:
    """结构化验证错误，附带字段名、消息与可序列化响应。"""

    def __init__(
        self,
        field: str,
        message: str,
        code: str = "validation_error",
        suggestion: str | None = None,
    ) -> None:
        self.field = field
        self.message = message
        self.code = code
        self.suggestion = suggestion

    def to_response(self) -> dict[str, Any]:
        """转换为可被 JSON 序列化的字典。"""
        response: dict[str, Any] = {
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }
        if self.suggestion:
            response["suggestion"] = self.suggestion
        return response

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"ValidationErrorDetail(field={self.field!r}, message={self.message!r})"


def validate_and_clean(
    value: Any,
    field_name: str = "value",
    max_length: int = MAX_GENERIC_TEXT_LENGTH,
) -> tuple[Any, ValidationErrorDetail | None]:
    """通用文本清洗与基础校验。

    规则：
      - 非字符串类型       → 错误
      - 空字符串/纯空白    → 错误
      - 含控制字符        → 错误
      - 超过 max_length   → 截断（不报错）
    """
    if not isinstance(value, str):
        return value, ValidationErrorDetail(
            field=field_name,
            message=f"{field_name} 必须是字符串",
            code="invalid_type",
        )
    stripped = value.strip()
    if not stripped:
        return value, ValidationErrorDetail(
            field=field_name,
            message=f"{field_name} 不能为空",
            code="empty_value",
        )
    # 禁止控制字符（保留 \n \r \t 用于多行内容）
    if any(ord(c) < 0x20 and c not in "\n\r\t" for c in stripped):
        return value, ValidationErrorDetail(
            field=field_name,
            message=f"{field_name} 包含非法控制字符",
            code="invalid_control_chars",
        )
    cleaned = stripped[:max_length]
    return cleaned, None


class MaterialValidator:
    """材料名称校验器。

    允许任意非空字符串（包含中英文、数字与常见符号），但限制：
      - 长度不超过 100
      - 禁止控制字符、HTML 注入字符、引号、反引号、分号、花括号、反斜杠
    """

    @staticmethod
    def validate(material: Any) -> ValidationErrorDetail | None:
        if not isinstance(material, str):
            return ValidationErrorDetail(
                field="material",
                message="材料必须是字符串",
                code="invalid_type",
            )
        stripped = material.strip()
        if not stripped:
            return ValidationErrorDetail(
                field="material",
                message="材料名称不能为空",
                code="empty_value",
            )
        if len(stripped) > MAX_MATERIAL_NAME_LENGTH:
            return ValidationErrorDetail(
                field="material",
                message=f"材料名称不能超过 {MAX_MATERIAL_NAME_LENGTH} 个字符",
                code="value_too_long",
            )
        if _FORBIDDEN_MATERIAL_CHARS.search(stripped):
            return ValidationErrorDetail(
                field="material",
                message="材料名称包含非法字符",
                code="invalid_chars",
            )
        return None


class SizeValidator:
    """尺寸校验器，输入期望是包含 length/width/height/diameter 的 dict。"""

    @staticmethod
    def validate(
        size: Any,
    ) -> tuple[dict[str, float] | None, ValidationErrorDetail | None]:
        if not isinstance(size, dict):
            return None, ValidationErrorDetail(
                field="size",
                message="尺寸必须是字典格式",
                code="invalid_type",
            )

        cleaned: dict[str, float] = {}
        for field in _SIZE_FIELDS:
            if field not in size:
                continue
            value = size[field]
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None, ValidationErrorDetail(
                    field=f"size.{field}",
                    message=f"尺寸字段 {field} 必须是数值",
                    code="invalid_type",
                )
            if value <= 0:
                return None, ValidationErrorDetail(
                    field=f"size.{field}",
                    message=f"尺寸字段 {field} 必须大于 0",
                    code="invalid_value",
                )
            if value > _SIZE_MAX_VALUE:
                return None, ValidationErrorDetail(
                    field=f"size.{field}",
                    message=f"尺寸字段 {field} 不能超过 {_SIZE_MAX_VALUE} mm",
                    code="value_too_large",
                )
            cleaned[field] = float(value)

        if not cleaned:
            return None, ValidationErrorDetail(
                field="size",
                message="至少需要提供一个尺寸字段 (length/width/height/diameter)",
                code="missing_field",
            )
        return cleaned, None


class ToleranceValidator:
    """公差等级校验器，期望 ISO 公差代号，例如 IT7、 IT8。"""

    @staticmethod
    def validate(tolerance: Any) -> "ValidationErrorDetail | str":
        if not isinstance(tolerance, str):
            return ValidationErrorDetail(
                field="tolerance",
                message="公差必须是字符串",
                code="invalid_type",
            )
        normalized = tolerance.strip().upper().replace(" ", "")
        if not normalized:
            return ValidationErrorDetail(
                field="tolerance",
                message="公差不能为空",
                code="empty_value",
            )
        if normalized not in _TOLERANCE_GRADES:
            return ValidationErrorDetail(
                field="tolerance",
                message=f"不支持的公差等级: {tolerance}",
                code="unsupported_tolerance",
                suggestion=f"支持的公差等级: {', '.join(_TOLERANCE_GRADES)}",
            )
        return normalized
