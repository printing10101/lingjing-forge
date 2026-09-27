"""NL2CAD 视觉回看校验器（借鉴 agent3dify 的 Render Verifier / nurb 的可视化校验）。

B-rep 拓扑校验只能保证「几何合法」，不能保证「形状正确」——孔位打错、
凸台/凹腔颠倒、基准面读偏，都能通过 isValid()。本模块在拓扑校验通过后
补一道视觉门禁：

    导出模型 → 多视图渲染（等轴/主视/俯视）→ VLM 对照描述判定 →
    判定不一致时把差异点作为反馈回传重生成闭环（nl2cad_llm）。

设计约束（与项目「AI 提案、规则裁决」口径一致）：
- 视觉结论是软门禁：mismatch 只在有重试预算时触发重生成；预算耗尽时
  接受拓扑合法的模型并在结果中如实标注，不硬拦；
- VLM 未配置 / 渲染失败 / 调用异常 / 判定不可解析 → 一律降级为
  skipped / error，绝不影响生成主流程；
- 默认关闭（env ``LNN_NL2CAD_VISUAL_CHECK=1`` 开启，沿用
  ``LNN_ORCHESTRATOR_LLM_REPAIR`` 的 opt-in 惯例），注入 vision_call
  时无视开关直接生效（测试与上层服务可显式决策）。

渲染实现：cadquery 回读导出文件 → tessellate → matplotlib（Agg）三视图
出一张 PNG。视图标题用英文，避免无 CJK 字体环境下渲染成豆腐块。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections.abc import Awaitable, Callable

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

#: 视觉回看开关的环境变量名（"1"/"true"/"on"/"yes" 视为开启）
ENV_VISUAL_CHECK = "LNN_NL2CAD_VISUAL_CHECK"

#: 传给 VLM 的单张渲染图大小上限（base64 前），超过则拒绝发送防止打爆请求
_MAX_IMAGE_BYTES = 2 * 1024 * 1024


class VisualCheckError(Exception):
    """视觉回看校验失败（渲染/调用层错误，调用方应降级而非中断）。"""


class VisualCheckNotConfigured(VisualCheckError):
    """未配置支持视觉的 LLM Provider。"""


class VisualRenderError(VisualCheckError):
    """模型渲染失败。"""


class VisualCheckStatus(str, Enum):
    """单次视觉校验结论。"""

    PASSED = "passed"
    MISMATCH = "mismatch"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class VisualCheckResult:
    """视觉回看校验结果。

    Attributes:
        status: 结论（passed / mismatch / skipped / error）。
        summary: 一句话结论（人类可读，随结果落盘）。
        issues: VLM 给出的差异点列表（mismatch 时非空，作为重生成反馈）。
        image_path: 渲染 PNG 路径（skipped 且未渲染时为 None）。
        raw_response: VLM 原始回复（诊断用）。
    """

    status: VisualCheckStatus
    summary: str = ""
    issues: list[str] = field(default_factory=list)
    image_path: str | None = None
    raw_response: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "issues": self.issues,
            "image_path": self.image_path,
        }


#: 视觉调用协议：(prompt, image_paths) → VLM 文本回复
VisionCall = Callable[[str, list[str]], Awaitable[str]]


def is_visual_check_enabled() -> bool:
    """读取环境开关；沿用 opt-in 惯例，默认关闭。"""
    return os.environ.get(ENV_VISUAL_CHECK, "").strip().lower() in {"1", "true", "on", "yes"}


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

# (elev, azim, 英文标题)：等轴 + 主视 + 俯视，覆盖形状判断所需的最小视角集
_VIEWS: tuple[tuple[float, float, str], ...] = (
    (30, -60, "ISO"),
    (0, -90, "FRONT"),
    (90, -90, "TOP"),
)


def render_model_views(model_path: str, out_png: str) -> str:
    """把导出的模型文件渲染成三视图 PNG，返回图片路径。

    支持 STEP/STL（NL2CAD 闭环实际只导出这两种）；其余格式抛
    :class:`VisualRenderError`。
    """
    path = Path(model_path)
    if not path.exists():
        raise VisualRenderError(f"模型文件不存在: {path}")

    # matplotlib 延迟导入：本模块被 nl2cad_llm 常驻导入，Agg 后端必须
    # 在 pyplot 之前指定，避免 FastAPI 进程里拉起 GUI 后端
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        import cadquery as cq
    except ImportError as e:
        raise VisualRenderError(f"渲染依赖不可用: {e}") from e

    suffix = path.suffix.lower()
    try:
        if suffix in {".step", ".stp"}:
            shape = cq.importers.importStep(str(path))
        elif suffix == ".stl":
            shape = cq.importers.importStl(str(path))
        else:
            raise VisualRenderError(f"不支持的渲染格式: {suffix}（仅支持 STEP/STL）")

        wp_shape = shape.val() if hasattr(shape, "val") else shape
        vertices, triangles = wp_shape.tessellate(0.5)
        if not triangles:
            raise VisualRenderError("网格为空：模型没有可渲染的三角面")
        verts = np.array([(v.x, v.y, v.z) for v in vertices], dtype=float)
        faces = verts[np.array(triangles, dtype=int)]

        # 简单兰伯特着色：按面法线与光照方向的夹角给灰度，增强立体感
        normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
        norm_len = np.linalg.norm(normals, axis=1)
        norm_len[norm_len == 0] = 1.0
        unit_normals = normals / norm_len[:, None]
        light = np.array([0.4, 0.3, 0.85])
        shade = 0.45 + 0.55 * np.clip(np.abs(unit_normals @ light), 0.0, 1.0)
        face_colors = np.stack([shade, shade, shade, np.ones_like(shade)], axis=1)

        fig = plt.figure(figsize=(12.0, 4.2), dpi=100)
        try:
            for i, (elev, azim, title) in enumerate(_VIEWS, start=1):
                ax = fig.add_subplot(1, len(_VIEWS), i, projection="3d")
                ax.add_collection3d(Poly3DCollection(faces, facecolors=face_colors, edgecolor="none"))
                _set_equal_box_aspect(ax, verts)
                ax.view_init(elev=elev, azim=azim)
                ax.set_title(title, fontsize=10)
                ax.set_axis_off()

            out = Path(out_png)
            out.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(out, bbox_inches="tight")
            logger.info("视觉校验渲染完成: %s（%d 面）", out, len(faces))
            return str(out)
        finally:
            # 渲染中途失败（如 add_collection3d 对退化网格抛错）也必须关闭
            # figure，否则 Agg 后端泄漏到长驻 FastAPI 进程
            plt.close(fig)
    except VisualRenderError:
        raise
    except Exception as e:  # noqa: BLE001 - OCCT/matplotlib 异常类型繁杂，渲染失败必须降级
        raise VisualRenderError(f"模型渲染失败: {type(e).__name__}: {e}") from e


def _set_equal_box_aspect(ax: Any, verts: "np.ndarray[Any, Any]") -> None:
    """三轴等比例并留 5% 边距，避免长条零件被画成方块。"""
    center = verts.mean(axis=0)
    extents = verts.max(axis=0) - verts.min(axis=0)
    max_extent = float(extents.max()) or 1.0
    ax.set_xlim(center[0] - max_extent / 2 * 1.05, center[0] + max_extent / 2 * 1.05)
    ax.set_ylim(center[1] - max_extent / 2 * 1.05, center[1] + max_extent / 2 * 1.05)
    ax.set_zlim(center[2] - max_extent / 2 * 1.05, center[2] + max_extent / 2 * 1.05)
    try:
        ax.set_box_aspect((1, 1, 1))
    except AttributeError:  # 老版本 matplotlib 无 set_box_aspect
        pass


# ---------------------------------------------------------------------------
# Prompt / 判定解析
# ---------------------------------------------------------------------------

_VISUAL_SYSTEM_PROMPT = (
    "你是机械零件图纸审查专家。给定一段零件的文字描述和一张由 3D 模型渲染出的"
    "三视图（ISO/FRONT/TOP），判断模型是否忠实实现了描述。"
    "只关注形状层面的重大偏差（特征缺失/多余、孔位错误、凹凸颠倒、比例明显失衡），"
    "不苛求渲染细节（着色、边缘锯齿、视角遮挡不算问题）。"
    "只输出 JSON，格式："
    '{"match": true/false, "issues": ["差异点1", ...], "confidence": 0.0~1.0}'
)

_VISUAL_USER_TEMPLATE = "零件描述：\n{description}\n\n请对照渲染三视图判断模型是否符合描述，只输出 JSON。"


def build_visual_check_prompt(description: str) -> str:
    """构造视觉校验的用户提示词（图片作为独立参数传给 VisionCall）。"""
    return _VISUAL_USER_TEMPLATE.format(description=description)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_visual_verdict(text: str) -> tuple[bool, list[str], float]:
    """从 VLM 回复中提取判定。

    Returns:
        (match, issues, confidence)。

    Raises:
        VisualCheckError: 回复中找不到可解析的 JSON 判定。
    """
    match_obj = _JSON_OBJECT_RE.search(text)
    if match_obj is None:
        raise VisualCheckError(f"VLM 回复中没有 JSON 判定: {text[:200]}")
    try:
        data = json.loads(match_obj.group(0))
    except json.JSONDecodeError as e:
        raise VisualCheckError(f"VLM 判定 JSON 解析失败: {e}") from e
    if not isinstance(data, dict) or "match" not in data:
        raise VisualCheckError(f"VLM 判定缺少 match 字段: {text[:200]}")
    issues_raw = data.get("issues", [])
    issues = [str(i) for i in issues_raw] if isinstance(issues_raw, list) else [str(issues_raw)]
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return bool(data["match"]), issues, confidence


# ---------------------------------------------------------------------------
# Provider 适配
# ---------------------------------------------------------------------------


def _encode_image_data_url(image_path: str) -> str:
    """PNG → data URL（OpenAI 多模态格式）。"""
    raw = Path(image_path).read_bytes()
    if len(raw) > _MAX_IMAGE_BYTES:
        raise VisualCheckError(f"渲染图过大（{len(raw)} 字节），拒绝发送")
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def make_vision_call_from_provider(provider: Any) -> VisionCall:
    """把声明了 VISION 能力的 Provider 适配为 VisionCall（OpenAI 多模态格式）。

    OpenAI 兼容协议的 content 支持分段数组（text + image_url），provider 层
    原样透传 payload，因此无需为每个云厂商单独适配。
    """
    capabilities = getattr(getattr(provider, "config", None), "capabilities", None) or []
    if not any(getattr(c, "value", c) == "vision" for c in capabilities):
        raise VisualCheckNotConfigured(f"Provider {getattr(provider, 'provider_id', '?')} 未声明 VISION 能力")

    async def _call(prompt: str, image_paths: list[str]) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for p in image_paths:
            content.append({"type": "image_url", "image_url": {"url": _encode_image_data_url(p)}})
        resp = await provider.chat_completion(
            [{"role": "user", "content": content}],
            max_tokens=512,
            temperature=0.0,
        )
        text = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        if not text:
            raise VisualCheckError("VLM 返回空内容")
        return text

    return _call


def get_default_vision_call() -> VisionCall:
    """从注册表取视觉调用：优先活跃 Provider，否则第一个启用且带 VISION 的。"""
    from app.ai.llm._registry import get_registry

    registry = get_registry()
    candidates: list[Any] = []
    active = registry.get_active_provider()
    if active is not None:
        candidates.append(active)
    active_config = registry.get_active_provider_config()
    for config in registry.list_providers(include_disabled=False):
        if active_config is not None and config.provider_id == active_config.provider_id:
            continue
        instance = registry.get_provider_instance(config.provider_id)
        if instance is not None:
            candidates.append(instance)

    for provider in candidates:
        try:
            return make_vision_call_from_provider(provider)
        except VisualCheckNotConfigured:
            continue
    raise VisualCheckNotConfigured(
        "未配置支持视觉的 LLM Provider（视觉回看不可用）。请配置 OpenAI/Gemini/Claude 等具备 VISION 能力的 Provider。"
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def run_visual_check(
    description: str,
    model_path: str,
    vision_call: VisionCall,
    out_png: str | None = None,
) -> VisualCheckResult:
    """渲染 + VLM 判定，任何失败都降级为 error/skipped 而不抛出。

    Args:
        description: 用户的自然语言零件描述（对照基准）。
        model_path: 已导出的模型文件（STEP/STL）。
        vision_call: 视觉调用（缺省由调用方先经 is_visual_check_enabled 决策）。
        out_png: 渲染输出路径（缺省放在模型同目录 ``<task>_views.png``）。
    """
    png_path = out_png or str(Path(model_path).with_name(Path(model_path).stem + "_views.png"))
    try:
        image_path = render_model_views(model_path, png_path)
    except VisualRenderError as e:
        logger.warning("视觉校验跳过（渲染失败）: %s", e)
        return VisualCheckResult(status=VisualCheckStatus.ERROR, summary=f"渲染失败: {e}")

    try:
        raw = await vision_call(build_visual_check_prompt(description), [image_path])
    except Exception as e:  # noqa: BLE001 - VLM 网络/配额异常统一降级
        logger.warning("视觉校验跳过（VLM 调用失败）: %s", e)
        return VisualCheckResult(
            status=VisualCheckStatus.ERROR,
            summary=f"VLM 调用失败: {e}",
            image_path=image_path,
        )

    try:
        match, issues, confidence = parse_visual_verdict(raw)
    except VisualCheckError as e:
        logger.warning("视觉校验跳过（判定不可解析）: %s", e)
        return VisualCheckResult(
            status=VisualCheckStatus.ERROR,
            summary=f"判定不可解析: {e}",
            image_path=image_path,
            raw_response=raw,
        )

    if match:
        return VisualCheckResult(
            status=VisualCheckStatus.PASSED,
            summary=f"VLM 判定模型与描述一致（confidence={confidence:.2f}）",
            image_path=image_path,
            raw_response=raw,
        )
    return VisualCheckResult(
        status=VisualCheckStatus.MISMATCH,
        summary=f"VLM 判定模型与描述不一致（confidence={confidence:.2f}）",
        issues=issues,
        image_path=image_path,
        raw_response=raw,
    )


__all__ = [
    "ENV_VISUAL_CHECK",
    "VisualCheckError",
    "VisualCheckNotConfigured",
    "VisualRenderError",
    "VisualCheckStatus",
    "VisualCheckResult",
    "VisionCall",
    "is_visual_check_enabled",
    "render_model_views",
    "build_visual_check_prompt",
    "parse_visual_verdict",
    "make_vision_call_from_provider",
    "get_default_vision_call",
    "run_visual_check",
]
