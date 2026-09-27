"""方言管理 API 路由（P3）。

对应 docs/development/postprocessor-方言声明化设计.md §5.1：
- ``GET  /api/v1/postprocessor/dialects`` — 列出已发现方言（内置 + 声明镜像）
- ``GET  /api/v1/postprocessor/dialects/{id}`` — 方言详情（声明 + 模板列表）
- ``GET  /api/v1/postprocessor/dialects/{id}/templates/{method}`` — 读取模板内容
- ``POST /api/v1/postprocessor/dialects/preview`` — NC 输出预览（杀手锏：给定
  样例刀路输入，渲染当前方言完整 NC 输出）

前端方言管理页 / 新建向导 / 实时预览器消费本 API。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.postprocessor.dialect import (
    DialectDeclaration,
    DialectRegistry,
)
from app.postprocessor.dialect.declaration import (
    ALLOWED_TEMPLATE_METHODS,
)
from app.postprocessor.dialect.registry import DEFAULT_DIALECT_PLUGIN_DIR

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/postprocessor/dialects",
    tags=["Postprocessor Dialects"],
)

# 内置方言（引擎自带，非声明镜像）
BUILTIN_DIALECTS: list[dict[str, Any]] = [
    {
        "id": "fanuc_0i",
        "name": "Fanuc 0i-MF",
        "version": "builtin",
        "extends": None,
        "source": "builtin",
        "template_methods": [],
    },
    {
        "id": "siemens_840d",
        "name": "Siemens 840D",
        "version": "builtin",
        "extends": None,
        "source": "builtin",
        "template_methods": [],
    },
    {
        "id": "heidenhain_tnc",
        "name": "Heidenhain TNC",
        "version": "builtin",
        "extends": None,
        "source": "builtin",
        "template_methods": [],
    },
    {
        "id": "xmachine_xm100",
        "name": "XMachine XM-100",
        "version": "builtin",
        "extends": None,
        "source": "builtin",
        "template_methods": [],
    },
]


def _default_plugin_root() -> Path:
    """默认方言插件根目录（仓库根 / postprocessor-plugins）。

    文件位于 app/api/v1/postprocessor_dialects.py →
    parents[0]=v1, [1]=api, [2]=app, [3]=python, [4]=engineering, [5]=仓库根
    """
    return Path(__file__).resolve().parents[5] / DEFAULT_DIALECT_PLUGIN_DIR


def _declaration_to_dict(decl: DialectDeclaration, source: str) -> dict[str, Any]:
    """声明 → 公开字典。"""
    return {
        "id": decl.id,
        "name": decl.name,
        "version": decl.version,
        "extends": decl.extends,
        "source": source,
        "template_methods": sorted(decl.templates.keys()),
        "params_keys": sorted(decl.params.keys()),
        "hooks": decl.hooks,
        "author": decl.author,
        "description": decl.description,
    }


@router.get("")
def list_dialects() -> dict[str, Any]:
    """列出所有方言（内置 + 声明镜像）。

    内置方言来自引擎（fanuc/siemens/heidenhain/xmachine）；
    声明镜像来自 ``postprocessor-plugins/*/dialect.yaml``。
    声明加载失败会降级记录（不阻断整体列表）。
    """
    registry = DialectRegistry(plugin_root=_default_plugin_root())
    found = registry.discover()
    registry.compile_all()

    entries: list[dict[str, Any]] = list(BUILTIN_DIALECTS)
    for dialect_id in found:
        decl = registry.get_declaration(dialect_id)
        if decl is not None:
            entries.append(_declaration_to_dict(decl, source="declared"))

    # 声明镜像优先展示在前
    entries.sort(key=lambda e: (0 if e["source"] == "declared" else 1, e["id"]))

    return success(
        data={
            "dialects": entries,
            "total": len(entries),
            "declared": len(found),
            "compile_errors": registry.get_compile_errors(),
        }
    )


@router.get("/{dialect_id}")
def get_dialect_detail(dialect_id: str) -> dict[str, Any]:
    """方言详情：声明 + 模板方法列表 + 编译状态。"""
    # 内置方言
    for entry in BUILTIN_DIALECTS:
        if entry["id"] == dialect_id:
            return success(data={**entry, "is_declared": False})

    # 声明镜像
    registry = DialectRegistry(plugin_root=_default_plugin_root())
    registry.discover()
    decl = registry.get_declaration(dialect_id)
    if decl is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"方言 '{dialect_id}' 不存在。建议操作：检查 postprocessor-plugins/"
            f"{dialect_id}/dialect.yaml 是否存在。",
        )
    registry.compile_all()
    errors = registry.get_compile_errors()
    return success(
        data={
            **_declaration_to_dict(decl, source="declared"),
            "is_declared": True,
            "compile_ok": dialect_id not in errors,
            "compile_error": errors.get(dialect_id),
            "templates": {method: str(path.name) for method, path in sorted(decl.templates.items())},
        }
    )


class TemplateReadRequest(BaseModel):
    """读取模板内容请求。"""

    dialect_id: str = Field(..., description="方言 id")
    method: str = Field(..., description="模板方法名（如 format_header）")


@router.post("/template")
def read_template(req: TemplateReadRequest) -> dict[str, Any]:
    """读取方言模板文件内容（工艺员编辑模板用）。"""
    if req.method not in ALLOWED_TEMPLATE_METHODS:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"模板方法 '{req.method}' 不在白名单内。建议操作：可选值 {sorted(ALLOWED_TEMPLATE_METHODS)}。",
        )
    registry = DialectRegistry(plugin_root=_default_plugin_root())
    registry.discover()
    decl = registry.get_declaration(req.dialect_id)
    if decl is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"方言 '{req.dialect_id}' 不存在（非声明式或未发现）。",
        )
    template_path = decl.templates.get(req.method)
    if template_path is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"方言 '{req.dialect_id}' 未声明模板方法 '{req.method}'（该方法继承基类实现）。",
        )
    try:
        content = template_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("[dialect.template] 读取失败: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"读取模板失败: {template_path}",
        )
    return success(
        data={
            "dialect_id": req.dialect_id,
            "method": req.method,
            "path": str(template_path),
            "content": content,
        }
    )


class PreviewRequest(BaseModel):
    """NC 输出预览请求。"""

    dialect_id: str = Field(..., description="方言 id（声明镜像或内置）")
    program_number: int = Field(1000, ge=1, le=9999, description="程序号")
    safe_z_height: float = Field(80.0, gt=0, description="安全高度")
    decimal_places: int = Field(3, ge=0, le=6, description="小数位数")


@router.post("/preview", dependencies=[Depends(require_permission("postprocessor:read"))])
def preview_dialect(req: PreviewRequest) -> dict[str, Any]:
    """NC 输出预览：给定样例刀路输入，渲染方言完整 NC 输出。

    样例序列与 golden 测试的标准序列一致（header → 换刀 → 补偿 → 冷却 →
    快移 → 直线 → 圆弧 → 钻孔 → 冷却关 → 程序尾），保证预览与真实行为一致。
    这是方言管理页 / 新建向导的「杀手锏」：工艺员改模板立刻看到输出。
    """
    from app.postprocessor.preview_sequence import build_standard_program

    # 解析方言类
    dialect_cls = _resolve_dialect_class(req.dialect_id)
    if dialect_cls is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"方言 '{req.dialect_id}' 无法解析。建议操作：检查是否为内置方言"
            "或 postprocessor-plugins/ 下存在有效 dialect.yaml。",
        )

    try:
        processor = dialect_cls(
            decimal_places=req.decimal_places,
            safe_z_height=req.safe_z_height,
        )
        # 用请求的程序号渲染标准序列（预览参数生效）
        output = build_standard_program(processor, program_number=req.program_number)
    except Exception as e:  # noqa: BLE001 - 预览失败返回可读错误，不抛 500
        logger.error("[dialect.preview] 方言 %s 预览失败: %s", req.dialect_id, e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"方言 '{req.dialect_id}' 预览失败: {e}",
        )

    return success(
        data={
            "dialect_id": req.dialect_id,
            "program_number": req.program_number,
            "output": output,
        },
        message=f"方言 '{req.dialect_id}' 预览生成成功",
    )


def _resolve_dialect_class(dialect_id: str):
    """解析方言 id 为可实例化的处理器类（内置或声明镜像）。"""
    from app.postprocessor.registry import PostProcessorRegistry

    # 声明镜像优先：加载并注册
    registry = DialectRegistry(plugin_root=_default_plugin_root())
    found = registry.discover()
    if dialect_id in found:
        try:
            registry.compile_all()
            registry.register_to(PostProcessorRegistry())
        except Exception:  # noqa: BLE001
            logger.warning("[dialect.preview] 声明方言 %s 编译注册失败，回退内置", dialect_id, exc_info=True)

    # 统一从 PostProcessorRegistry 取（内置 + 已注册声明）
    try:
        return type(PostProcessorRegistry().get_processor(dialect_id))
    except KeyError:
        return None


# 写路径：新建 / 保存模板 / 删除（工艺员自由度闭环；写操作需 plugin:config:update）


class CreateDialectRequest(BaseModel):
    """新建方言请求。"""

    id: str = Field(..., pattern=r"^[a-z0-9_]{3,64}$", description="方言 id（小写字母/数字/下划线）")
    name: str = Field(..., min_length=1, max_length=120, description="可读名称")
    extends: str = Field(..., description="继承的基类方言 id（如 fanuc_0i）")
    description: str = Field("", max_length=500, description="描述")
    author: str = Field("", max_length=120, description="作者")


class SaveTemplateRequest(BaseModel):
    """保存模板内容请求。"""

    dialect_id: str = Field(..., description="方言 id")
    method: str = Field(..., description="模板方法名（如 format_header）")
    content: str = Field(..., description="模板内容（Jinja2）")
    max_length: int = Field(64 * 1024, description="模板最大字节数（防超大文件）")


def _validate_dialect_id(dialect_id: str, plugin_root: Path) -> None:
    """校验方言 id 合法性 + 禁止目录穿越。

    方言 id 仅允许 [a-z0-9_]，且目标目录必须是 plugin_root 的直接子目录，
    从根上杜绝 path traversal（../../ 等）。
    """
    import re

    if not re.fullmatch(r"[a-z0-9_]{3,64}", dialect_id):
        raise ValueError(f"方言 id '{dialect_id}' 不合法。建议操作：仅允许小写字母/数字/下划线，3-64 字符。")
    target = plugin_root / dialect_id
    if target.parent != plugin_root:
        raise ValueError("方言目录必须位于插件根目录下。")


@router.post("", dependencies=[Depends(require_permission("plugin:config:update"))])
def create_dialect(req: CreateDialectRequest) -> dict[str, Any]:
    """新建声明式方言：创建目录 + dialect.yaml + 骨架模板。

    骨架模板从继承基类的默认实现生成（header/footer 等），工艺员随后在
    页面编辑模板并实时预览。这是「工艺员零代码加方言」的完整闭环入口。
    """
    plugin_root = _default_plugin_root()
    try:
        _validate_dialect_id(req.id, plugin_root)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    # extends 白名单校验
    from app.postprocessor.dialect.declaration import BUILTIN_BASE_DIALECTS

    if req.extends not in BUILTIN_BASE_DIALECTS:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"extends='{req.extends}' 不是受支持的内置方言（可选值: {sorted(BUILTIN_BASE_DIALECTS)}）",
        )

    target_dir = plugin_root / req.id
    if target_dir.exists():
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"方言 '{req.id}' 已存在。建议操作：换一个 id，或先删除再重建。",
        )

    try:
        target_dir.mkdir(parents=True, exist_ok=False)
        (target_dir / "templates").mkdir(parents=True, exist_ok=False)
        _write_dialect_yaml(target_dir, req)
        _write_skeleton_templates(target_dir, req.extends)
    except OSError as e:
        logger.error("[dialect.create] 创建失败: %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=f"方言 '{req.id}' 创建失败: {e}")

    logger.info("方言创建成功: %s (extends=%s)", req.id, req.extends)
    return success(
        data={"id": req.id, "name": req.name, "extends": req.extends},
        message=f"方言 '{req.id}' 创建成功，可在页面编辑模板并预览",
    )


def _write_dialect_yaml(target_dir: Path, req: CreateDialectRequest) -> None:
    """写入 dialect.yaml（骨架声明 header/footer 模板，工艺员按需补充）。"""
    yaml_content = (
        "# =============================================================================\n"
        "# 方言声明：" + req.name + "\n"
        "# =============================================================================\n"
        "# 本文件由「新建方言向导」生成。骨架已声明 header/footer 模板，\n"
        "# 工艺员在页面编辑并实时预览；按需补充其他 templates 覆盖项，\n"
        "# 未声明的模板方法继承 extends 基类实现（零 Python）。\n"
        "# =============================================================================\n\n"
        f"id: {req.id}\n"
        f"name: {req.name}\n"
        'version: "1.0.0"\n'
        f"extends: {req.extends}\n"
        f"target_controller: {req.id}\n\n"
        "templates:\n"
        "  format_header: templates/header.j2\n"
        "  format_footer: templates/footer.j2\n\n"
        "params: {}\n\n"
        f"author: {req.author or 'Lingjing Manufacturing Team'}\n"
        f"description: {req.description}\n"
    )
    (target_dir / "dialect.yaml").write_text(yaml_content, encoding="utf-8")


def _write_skeleton_templates(target_dir: Path, extends: str) -> None:
    """写入骨架模板：header.j2 / footer.j2（参数化 Jinja2，非硬编码）。

    骨架基于继承方言的默认输出格式，把可变值（程序号/安全高度/转速/坐标系）
    转为模板变量引用，工艺员在此基础上修改并实时预览。
    """
    from app.postprocessor.dialect.compiler import _load_builtin_dialect_classes
    from app.postprocessor.preview_sequence import build_standard_program

    base_cls = _load_builtin_dialect_classes()[extends]
    processor = base_cls()
    output = build_standard_program(processor, program_number=1000)

    lines = output.split("\n")

    # header：截取到换刀块前（含 M08 冷却开；找不到 M08 时兜底用全输出前半）
    header_lines = []
    for line in lines:
        header_lines.append(line)
        if "M08" in line:
            break
    if not any("M08" in line for line in header_lines):
        header_lines = lines[: max(1, len(lines) // 2)]

    # footer：从冷却关 M09 到结尾
    footer_lines = []
    in_footer = False
    for line in lines:
        if "M09" in line and not in_footer:
            in_footer = True
        if in_footer:
            footer_lines.append(line)

    (target_dir / "templates" / "header.j2").write_text(_parametrize_header(header_lines, processor), encoding="utf-8")
    (target_dir / "templates" / "footer.j2").write_text(_parametrize_footer(footer_lines), encoding="utf-8")


def _parametrize_header(header_lines: list, processor) -> str:
    """把骨架 header 的硬编码值转为 Jinja2 变量引用。

    转换规则：
    - ``O1000 (PROGRAM 1000 - ...)`` → 程序号用 ``{{ program_number }}``
    - ``Z80.000``（safe_z_height 格式化值）→ ``{{ pp.safe_z_height | fmt }}``
    - ``S1000``（默认转速）→ ``{{ pp.get_spindle_rpm() | int }}``
    - ``G54``（默认坐标系）→ ``{{ pp._default_coordinate_system }}``
    """
    safe_z = processor._fmt(processor.safe_z_height)
    default_rpm = str(int(processor.get_spindle_rpm()))
    wcs = processor._default_coordinate_system
    date = processor._date_string()

    # 程序号：O1000 / O1000 (PROGRAM 1000 - date)
    import re

    parametrized = []
    for line in header_lines:
        new_line = line
        # 程序号行：O\d{4} 与 (PROGRAM \d+)
        new_line = re.sub(r"O\d{4}", 'O{{ "%04d" | format(program_number) }}', new_line)
        new_line = re.sub(
            r"\(PROGRAM \d+",
            "(PROGRAM {{ program_number }}",
            new_line,
        )
        # 日期：用 pp._date_string()（黄金测试固定日期时一致）
        if date and date in new_line:
            new_line = new_line.replace(date, "{{ pp._date_string() }}")
        # 安全高度
        new_line = new_line.replace(f"Z{safe_z}", "Z{{ pp.safe_z_height | fmt }}")
        # 默认转速
        new_line = new_line.replace(f"S{default_rpm}", "S{{ pp.get_spindle_rpm() | int }}")
        # 坐标系
        new_line = new_line.replace(f" {wcs} ", " {{ pp._default_coordinate_system }} ")
        parametrized.append(new_line)

    return "\n".join(parametrized) + "\n"


def _parametrize_footer(footer_lines: list) -> str:
    """footer 骨架：大部分是固定指令（M09/M05/回零/M30），直接保留。"""
    return "\n".join(footer_lines) + "\n"


@router.put("/{dialect_id}/template", dependencies=[Depends(require_permission("plugin:config:update"))])
def save_template(dialect_id: str, req: SaveTemplateRequest) -> dict[str, Any]:
    """保存方言模板内容（写回模板文件）。

    安全约束：
    - dialect_id 严格白名单校验（防目录穿越）
    - 仅允许覆盖已声明的方法（禁止创建任意文件）
    - 内容大小上限（防超大文件写盘）
    """
    plugin_root = _default_plugin_root()
    try:
        _validate_dialect_id(dialect_id, plugin_root)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    # 方言必须存在且是声明式
    registry = DialectRegistry(plugin_root=plugin_root)
    registry.discover()
    decl = registry.get_declaration(dialect_id)
    if decl is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"方言 '{dialect_id}' 不存在或非声明式。建议操作：先通过新建向导创建。",
        )

    template_path = decl.templates.get(req.method)
    if template_path is None:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"方言 '{dialect_id}' 未声明模板方法 '{req.method}'。"
            "建议操作：仅可保存已声明的方法（见方言详情的 template_methods）。",
        )

    # 内容大小校验
    content_bytes = req.content.encode("utf-8")
    if len(content_bytes) > req.max_length:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"模板内容过大（{len(content_bytes)} 字节，上限 {req.max_length}）。",
        )

    try:
        template_path.write_text(req.content, encoding="utf-8")
    except OSError as e:
        logger.error("[dialect.save] 保存失败: %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=f"模板保存失败: {e}")

    logger.info("模板已保存: %s/%s", dialect_id, req.method)
    return success(
        data={"dialect_id": dialect_id, "method": req.method, "path": str(template_path)},
        message=f"模板 '{req.method}' 保存成功",
    )


@router.delete("/{dialect_id}", dependencies=[Depends(require_permission("plugin:config:update"))])
def delete_dialect(dialect_id: str) -> dict[str, Any]:
    """删除声明式方言（仅限 postprocessor-plugins/ 下的非内置方言）。"""
    plugin_root = _default_plugin_root()
    try:
        _validate_dialect_id(dialect_id, plugin_root)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    # 内置方言不可删
    builtin_ids = {d["id"] for d in BUILTIN_DIALECTS}
    if dialect_id in builtin_ids:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"方言 '{dialect_id}' 是内置方言，不可删除。",
        )

    target_dir = plugin_root / dialect_id
    if not target_dir.is_dir():
        return error(code=ErrorCode.NOT_FOUND, message=f"方言 '{dialect_id}' 不存在。")

    try:
        import shutil

        shutil.rmtree(target_dir)
    except OSError as e:
        logger.error("[dialect.delete] 删除失败: %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=f"方言 '{dialect_id}' 删除失败: {e}")

    logger.info("方言已删除: %s", dialect_id)
    return success(data={"id": dialect_id}, message=f"方言 '{dialect_id}' 已删除")


# 参数读写（遗留项⑤：工艺员在页面调参数，而非改 YAML）


class SaveParamsRequest(BaseModel):
    """保存方言参数请求。"""

    dialect_id: str = Field(..., description="方言 id")
    params: dict[str, Any] = Field(default_factory=dict, description="方言自己的参数（覆盖继承值）")


@router.get("/{dialect_id}/params")
def get_dialect_params(dialect_id: str) -> dict[str, Any]:
    """读取方言参数：有效配置（继承链合并）+ 方言自己的参数。

    有效配置 = base（postprocessor_config.yaml 的 base 段）深合并方言 params，
    与 ConfigLoader 的 _deep_merge 语义一致。前端展示有效值，编辑方言层覆盖。
    """
    plugin_root = _default_plugin_root()
    try:
        _validate_dialect_id(dialect_id, plugin_root)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    registry = DialectRegistry(plugin_root=plugin_root)
    registry.discover()
    decl = registry.get_declaration(dialect_id)
    if decl is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"方言 '{dialect_id}' 不存在或非声明式。建议操作：先通过新建向导创建。",
        )

    from app.postprocessor._loader import _deep_merge
    from app.postprocessor.config_loader import ConfigLoader

    # 加载全局 base 配置（postprocessor_config.yaml 的 base 段）
    import yaml  # type: ignore[import-untyped]

    try:
        loader = ConfigLoader()
        resolved = loader._resolve_path(None)
        with open(resolved, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        base_config = raw.get("base", {}) if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001 - base 配置加载失败时退化为空
        logger.warning("[dialect.params] base 配置加载失败，退化为空", exc_info=True)
        base_config = {}

    effective = _deep_merge(base_config, decl.params or {})

    return success(
        data={
            "dialect_id": dialect_id,
            "effective": effective,  # base + 方言覆盖后的有效配置
            "dialect_params": decl.params or {},  # 方言自己的（可编辑层）
            "base_keys": sorted(base_config.keys()),
        }
    )


@router.put(
    "/{dialect_id}/params",
    dependencies=[Depends(require_permission("plugin:config:update"))],
)
def save_dialect_params(dialect_id: str, req: SaveParamsRequest) -> dict[str, Any]:
    """保存方言参数：写回 dialect.yaml 的 params 段。

    只写方言自己的 params 层（覆盖继承值），base 配置不被修改。
    通过 yaml 安全加载-修改-写回，保留 dialect.yaml 其它字段。
    """
    plugin_root = _default_plugin_root()
    try:
        _validate_dialect_id(dialect_id, plugin_root)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    yaml_path = plugin_root / dialect_id / "dialect.yaml"
    if not yaml_path.exists():
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"方言 '{dialect_id}' 不存在。建议操作：先通过新建向导创建。",
        )

    import yaml

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw["params"] = req.params or {}
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except (yaml.YAMLError, OSError) as e:
        logger.error("[dialect.params.save] 保存失败: %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=f"方言 '{dialect_id}' 参数保存失败: {e}")

    logger.info("方言参数已保存: %s (%d 顶层键)", dialect_id, len(req.params or {}))
    return success(
        data={"dialect_id": dialect_id, "params": req.params or {}},
        message=f"方言 '{dialect_id}' 参数保存成功",
    )


__all__ = ["router"]
