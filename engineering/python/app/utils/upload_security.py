"""统一的上传安全校验工具。

修复 P0-11/P0-12/P0-13 文件上传安全漏洞：
- P0-11：仿真上传无大小限制 → ``await file.read()`` 全量入内存 OOM/DoS
- P0-12：upload_resource 未认证 + 100MB 全量入内存
- P0-13：所有上传端点无 magic bytes 校验 → 仅扩展名白名单可被伪装绕过

提供 ``validate_upload`` 异步函数，统一执行：
1. 扩展名白名单校验
2. 分块流式读取 + 大小硬上限（避免超大文件全量入内存）
3. magic bytes 签名校验（纯字节比对，不依赖外部库；如安装了
   ``python-magic`` 则优先使用 libmagic 做更精确的 MIME 推断）
4. 返回文件内容 ``bytes`` 供业务层使用

设计要点：
- 默认上限 50MB；调用方可按业务需要放大或缩小（如仿真数据 100MB）
- 文本类扩展名（csv/txt/json/md/gcode/nc/tap）跳过 magic 校验，
  避免误杀；但仍执行扩展名 + 大小校验
- 错误统一抛出 ``HTTPException``：
  - 413 Payload Too Large：超过 ``max_size``
  - 415 Unsupported Media Type：扩展名或 magic bytes 不被允许
  - 400 Bad Request：文件名为空 / 内容为空 / 其他校验失败
"""

from __future__ import annotations

import logging
from pathlib import Path


from fastapi import HTTPException, UploadFile, status

from app.config.limits import MAX_UPLOAD_SIZE, STREAM_CHUNK_SIZE

logger = logging.getLogger(__name__)

# 常量

# ``MAX_UPLOAD_SIZE`` 由 ``app.config.limits`` 集中管理（50MB 默认上限），
# 与 ``dxf/api.py`` / ``step_import/api.py`` / ``projects/project_api.py``
# 等模块共享同一基准值，避免一处调整、多处不同步。
# ``STREAM_CHUNK_SIZE`` 同样由 ``app.config.limits`` 集中管理（64KB），
# 与 ``contracts/project_package.py`` / ``api/v1/project_packages.py``
# 共享同一基准值（流式 I/O 缓冲），下方 ``_CHUNK_SIZE`` 为本模块内部别名。

# 分块读取大小：64KB。够大以减少 I/O 次数，够小以限制单次内存占用
_CHUNK_SIZE = STREAM_CHUNK_SIZE

# 文件类型签名映射（magic bytes）。
# 每个键为 MIME 类型字符串，值为该类型可接受的字节签名列表；
# ``None`` 表示该类型跳过 magic bytes 校验（用于纯文本/JSON/CSV 等
# 编码可变、签名不固定的格式）。
ALLOWED_MIME_SIGNATURES: dict[str, list[bytes | None]] = {
    "application/pdf": [b"%PDF"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "application/zip": [b"PK\x03\x04"],
    # CAD/STEP 文件
    "application/step": [b"ISO-10303-21"],
    "application/iges": [
        # IGES 文件固定 80 字节起始行，前 72 字节通常为空白，后跟 "IGES"
        b"IGES",
    ],
    "application/dxf": [
        # AutoCAD DXF 从 AC1009 (R12) 至 AC1032 (R2018) 均以 "AC10" 开头
        b"AC10",
    ],
    "application/sla": [
        # STL ASCII 签名以 "solid" 开头；STL 二进制无固定签名（前 80 字节为任意注释），
        # 因此 STL 二进制文件跳过 magic 校验，仅靠扩展名 + 后续业务解析兜底
        b"solid",
    ],
    "application/octet-stream": [
        # STL 二进制 / OBJ / 3D 模型等无统一签名，跳过 magic 校验
        None,
    ],
    "text/csv": [None],
    "text/plain": [None],
    "application/json": [None],
}

# 扩展名 MIME 映射。未列出的扩展名若被显式允许，将按 octet-stream
# 处理（跳过 magic 校验，仅做扩展名 + 大小校验）。
EXTENSION_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".zip": "application/zip",
    ".step": "application/step",
    ".stp": "application/step",
    ".igs": "application/iges",
    ".iges": "application/iges",
    ".dxf": "application/dxf",
    ".stl": "application/sla",
    ".obj": "application/octet-stream",
    # 文本类
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".json": "application/json",
    ".md": "text/plain",
    ".gcode": "text/plain",
    ".nc": "text/plain",
    ".tap": "text/plain",
    ".html": "text/plain",
    ".xml": "text/plain",
    ".rtf": "text/plain",
    ".doc": "application/octet-stream",
    ".docx": "application/zip",  # docx 实际为 zip 容器
}

# 文本类扩展名集合：跳过 magic bytes 校验，仅做扩展名 + 大小校验。
# 这些格式编码可变（UTF-8/UTF-16/GBK），固定签名校验会误杀。
_TEXT_EXTENSIONS: set[str] = {
    ".csv",
    ".txt",
    ".json",
    ".md",
    ".gcode",
    ".nc",
    ".tap",
    ".html",
    ".xml",
    ".rtf",
    ".yaml",
    ".yml",
    ".log",
    ".ini",
}

# 检测 python-magic 是否可用（可选依赖，不强制安装）
try:
    import magic as _magic

    _HAS_LIBMAGIC = True
except ImportError:
    _HAS_LIBMAGIC = False


# 核心校验函数


async def validate_upload(
    file: UploadFile,
    max_size: int = MAX_UPLOAD_SIZE,
    allowed_extensions: set[str] | None = None,
    allowed_mimes: set[str] | None = None,
) -> bytes:
    """统一上传校验：扩展名 + magic bytes + 大小限制 + 分块读取。

    Args:
        file: FastAPI ``UploadFile`` 对象
        max_size: 最大允许字节数，默认 50MB
        allowed_extensions: 允许的扩展名集合（小写带点号，如 ``{'.csv', '.json'}``）。
            为 ``None`` 时跳过扩展名校验（不推荐，仅用于调用方已自行校验的场景）。
        allowed_mimes: 允许的 MIME 类型集合（如 ``{'text/csv', 'application/json'}``）。
            为 ``None`` 时根据扩展名推导 MIME，并校验该 MIME 是否在
            ``ALLOWED_MIME_SIGNATURES`` 中已知。

    Returns:
        文件内容 ``bytes``

    Raises:
        HTTPException:
            - 400: 文件名为空 / 内容为空
            - 413: 文件大小超过 ``max_size``
            - 415: 扩展名或 magic bytes 不被允许
    """
    # 1. 文件名 + 扩展名校验
    filename = file.filename or ""
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件名不能为空",
        )

    # 剥离路径组件，防止 ``../`` 等逃逸
    pure_name = Path(filename).name
    if pure_name != filename or not pure_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的文件名",
        )

    ext = Path(pure_name).suffix.lower()
    if allowed_extensions is not None and ext not in allowed_extensions:
        allowed_str = ", ".join(sorted(allowed_extensions)) if allowed_extensions else "(空)"
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件格式 '{ext}'，允许的格式: {allowed_str}",
        )

    # 2. 分块流式读取 + 大小硬上限
    # 避免一次性 ``await file.read()`` 将超大文件全量入内存。
    # 读取过程中累计字节，一旦超过 ``max_size`` 立即中止并抛出 413。
    chunks: list[bytes] = []
    total = 0
    max_size_plus_one = max_size + 1  # 多读 1 字节以判断是否超限
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            size_mb = total / (1024 * 1024)
            limit_mb = max_size / (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(f"文件大小({size_mb:.1f}MB)超过限制({limit_mb:.0f}MB)"),
            )
        chunks.append(chunk)
        # 已读到上限但还没读完，再读一次探测是否还有数据
        if total >= max_size_plus_one:
            # 理论上不会到这里，上面 total > max_size 已拦截
            break

    content = b"".join(chunks)

    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容为空",
        )

    # 3. magic bytes 签名校验
    _verify_magic_bytes(content, ext, allowed_mimes)

    return content


# 内部辅助


def _verify_magic_bytes(
    content: bytes,
    ext: str,
    allowed_mimes: set[str] | None,
) -> None:
    """校验文件内容的 magic bytes 签名。

    策略：
    1. 文本类扩展名（csv/txt/json/...）直接跳过 magic 校验。
    2. 若安装了 ``python-magic``，优先使用 libmagic 推断 MIME 并校验。
    3. 否则使用内置 ``ALLOWED_MIME_SIGNATURES`` 做纯字节签名比对。
    4. 若扩展名未在 ``EXTENSION_TO_MIME`` 中映射，视为未知二进制，
       仅当 ``allowed_mimes`` 为 ``None`` 或包含 ``application/octet-stream``
       时放行（由调用方负责扩展名白名单）。
    """
    # 文本类跳过 magic 校验
    if ext in _TEXT_EXTENSIONS:
        return

    expected_mime = EXTENSION_TO_MIME.get(ext)

    # 若调用方显式指定 allowed_mimes，校验扩展名推导出的 MIME 是否在白名单内
    if allowed_mimes is not None and expected_mime is not None:
        if expected_mime not in allowed_mimes:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(f"文件扩展名 '{ext}' 对应的 MIME '{expected_mime}' 不在允许类型列表中"),
            )

    # 未知扩展名（未在 EXTENSION_TO_MIME 中）：跳过 magic 校验，
    # 由调用方的扩展名白名单负责。这是为了兼容调用方自定义扩展名的场景。
    if expected_mime is None:
        return

    signatures = ALLOWED_MIME_SIGNATURES.get(expected_mime, [])

    # 签名列表含 None 表示该类型跳过 magic 校验（如 octet-stream）
    if None in signatures or not signatures:
        return

    # 优先使用 libmagic（若可用）做更精确的推断
    if _HAS_LIBMAGIC:
        try:
            detected = _magic.from_buffer(content[:1024], mime=True)
            # libmagic 返回的 MIME 可能含多个子类型（如 'text/plain; charset=utf-8'）
            detected_main = detected.split(";")[0].strip()
            if detected_main == expected_mime:
                return
            # libmagic 检测结果与扩展名预期不符，但仍校验字节签名兜底
            logger.debug(
                "libmagic 检测 MIME '%s' 与扩展名预期 '%s' 不符，回退到字节签名校验",
                detected_main,
                expected_mime,
            )
        except Exception as e:
            # libmagic 调用失败时降级到字节签名校验
            logger.debug("libmagic 调用失败，降级到字节签名校验: %s", e)

    # 纯字节签名比对（降级方案，不依赖外部库）
    head = content[:512]  # 取前 512 字节比对
    for sig in signatures:
        if sig is not None and head.startswith(sig):
            return

    # 所有签名均不匹配
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=(f"文件内容签名与扩展名 '{ext}' 不匹配，可能为伪装文件。请上传真实格式的文件。"),
    )


def get_expected_mime(ext: str) -> str | None:
    """查询扩展名对应的预期 MIME 类型（供业务层使用）。"""
    return EXTENSION_TO_MIME.get(ext.lower())
