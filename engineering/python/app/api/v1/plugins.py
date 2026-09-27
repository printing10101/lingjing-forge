from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth.permissions import require_permission
from app.capability.capability_gating import CapabilityGatekeeper
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.dependencies import get_plugin_manager
from app.plugins.plugin_manager import get_dependency_resolver
from app.plugins.plugin_types import PluginStatus
from app.plugins.plugin_worker import PluginWorkerManager, WorkerConfig

logger = logging.getLogger(__name__)

# 注意：本 router 尚未接入（main/router_registry 未引用本文件），路由未挂载。
router = APIRouter(
    prefix="/api/v1/plugins",
    tags=["Plugins (Capability Marketplace)"],
)


# 内置插件包目录（源码内嵌，作为本地市场的真实条目来源）
_MARKET_BUILTIN_DIR = Path(__file__).resolve().parents[2] / "plugins"

# 本地方言插件目录（P4：声明式方言插件以插件形态暴露到统一市场）
_DIALECT_PLUGIN_DIR = Path(__file__).resolve().parents[5] / "postprocessor-plugins"


def _friendly_name(raw: str) -> str:
    """将插件目录名转为可读名称（skill_loader -> Skill Loader）。"""
    return " ".join(word.capitalize() for word in raw.replace("_", " ").split())


def _builtin_market_entry(dir_name: str) -> dict:
    """构造内置插件包的市场条目（真实文件系统扫描结果）。"""
    return {
        "id": dir_name,
        "name": _friendly_name(dir_name),
        "version": "builtin",
        "author": "Lingjing",
        "description": f"本地内置插件包：{dir_name}",
        "plugin_type": "builtin",
        "installed": False,
        "status": None,
        "entry_point": "",
        "capabilities": [],
    }


def _dialect_market_entry(dialect_id: str) -> dict:
    """构造方言插件市场条目（真实 dialect.yaml 扫描结果）。

    方言插件 id 使用 ``dialect:<id>`` 前缀，与统一插件市场 id 空间隔离，
    避免与 app/plugins/ 下的业务插件冲突。plugin_type 标记为 postprocessor。
    """
    dialect_dir = _DIALECT_PLUGIN_DIR / dialect_id
    declaration = None
    try:
        from app.postprocessor.dialect.declaration import DialectDeclaration

        declaration = DialectDeclaration.from_yaml(dialect_dir / "dialect.yaml")
    except Exception:  # noqa: BLE001 - 扫描降级：声明损坏时显示基础信息
        logger.warning("[plugins.marketplace] 方言声明读取失败: %s", dialect_id, exc_info=True)

    return {
        "id": f"dialect:{dialect_id}",
        "name": declaration.name if declaration else _friendly_name(dialect_id),
        "version": declaration.version if declaration else "unknown",
        "author": declaration.author if declaration else "Lingjing",
        "description": (declaration.description if declaration else f"声明式后处理器方言：{dialect_id}"),
        "plugin_type": "postprocessor",
        "category": "dialect",
        "installed": False,
        "status": None,
        "entry_point": "dialect.yaml",
        "capabilities": [],
        "extends": declaration.extends if declaration else None,
        "template_methods": sorted(declaration.templates.keys()) if declaration else [],
    }


def _scan_dialect_plugins() -> list[dict]:
    """扫描本地方言插件目录，返回方言插件市场条目。"""
    if not _DIALECT_PLUGIN_DIR.is_dir():
        return []
    entries: list[dict] = []
    for child in sorted(_DIALECT_PLUGIN_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if not (child / "dialect.yaml").exists():
            continue
        entries.append(_dialect_market_entry(child.name))
    return entries


@router.get("/marketplace")
def list_marketplace_plugins(
    query: str | None = Query(None, description="Search query"),
    plugin_type: str | None = Query(None, description="Filter by type"),
    page: int = Query(1, ge=1, le=500),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取插件市场列表。

    数据来源为真实本地状态：
    1. 已注册插件（来自插件注册表，含真实状态）
    2. 内置插件包目录扫描（app/plugins/ 下的源码插件包，标记为未安装）
    """
    try:
        manager = get_plugin_manager()
        registry = manager._registry
        installed = registry.list_plugins()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        safe = safe_error_message(e, context="plugins.marketplace", fallback="插件市场查询失败，请稍后重试")
        logger.error("[plugins.marketplace] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        installed = []

    installed_map: dict[str, dict] = {}
    for p in installed:
        d = p.to_dict()
        d["installed"] = True
        installed_map[d["id"]] = d

    entries: list[dict] = []

    # 1. 已注册插件（真实）
    entries.extend(sorted(installed_map.values(), key=lambda x: x["id"]))

    # 2. 内置插件包目录扫描（真实文件系统）
    if _MARKET_BUILTIN_DIR.is_dir():
        for child in sorted(_MARKET_BUILTIN_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if child.name in installed_map:
                continue
            entries.append(_builtin_market_entry(child.name))

    # 3. 本地方言插件目录扫描（P4：声明式方言插件暴露到统一市场）
    entries.extend(_scan_dialect_plugins())

    # 过滤
    if query:
        q = query.lower()
        entries = [
            e
            for e in entries
            if q in e["id"].lower() or q in e["name"].lower() or q in e.get("description", "").lower()
        ]
    if plugin_type:
        entries = [e for e in entries if e.get("plugin_type") == plugin_type]

    total = len(entries)
    start = (page - 1) * page_size
    return success(
        data={
            "plugins": entries[start : start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.post("/marketplace/{plugin_id}/install", dependencies=[Depends(require_permission("plugin:config:update"))])
def install_marketplace_plugin(plugin_id: str):
    """安装市场插件：对已注册插件执行真实启用；内置源码包提示直接启用。"""
    manager = get_plugin_manager()
    try:
        info = manager.get_plugin_info(plugin_id)
    except KeyError:
        # 未注册：若为内置源码包，说明随应用内置，无法动态安装
        builtin_dir = _MARKET_BUILTIN_DIR / plugin_id
        if builtin_dir.is_dir():
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"插件 '{plugin_id}' 为源码内置模块，已随应用安装，请在插件管理中启用",
            )
        return error(code=ErrorCode.NOT_FOUND, message=f"插件 '{plugin_id}' 不存在")

    if info.get("status") == PluginStatus.ENABLED.value:
        return success(
            data={"installed": True, "status": PluginStatus.ENABLED.value},
            message=f"插件 '{plugin_id}' 已处于启用状态",
        )

    try:
        manager.enable_plugin(plugin_id)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        safe = safe_error_message(e, context="plugins.install", fallback=f"插件 '{plugin_id}' 安装失败")
        logger.error("[plugins.install] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"])

    return success(
        data={"installed": True, "status": PluginStatus.ENABLED.value},
        message=f"插件 '{plugin_id}' 安装并启用成功",
    )


@router.get("")
def list_installed_plugins(
    status: str | None = Query(None, description="Filter by status"),
    plugin_type: str | None = Query(None, description="Filter by type"),
    capability: str | None = Query(None, description="Filter by capability"),
):
    try:
        manager = get_plugin_manager()
        registry = manager._registry

        status_filter = PluginStatus(status) if status else None
        plugins = registry.list_plugins(
            status=status_filter,
            plugin_type=plugin_type,
            capability=capability,
        )

        return success(
            data={
                "plugins": [p.to_dict() for p in plugins],
                "total": len(plugins),
            }
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.list_installed", fallback="插件列表查询失败，请稍后重试")
        logger.error("[plugins.list_installed] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/{plugin_id}")
def get_plugin_detail(plugin_id: str):
    try:
        manager = get_plugin_manager()
        info = manager.get_plugin_info(plugin_id)

        resolver = get_dependency_resolver()
        info["dependency_tree"] = resolver.get_dependency_tree(plugin_id)

        gatekeeper = CapabilityGatekeeper.get_instance()
        info["capabilities"] = gatekeeper.get_plugin_capabilities(plugin_id)

        worker_info = None
        try:
            worker_mgr = PluginWorkerManager.get_instance()
            worker_info = worker_mgr.get_worker_info(plugin_id)
        except (RuntimeError, AttributeError, KeyError) as e:
            # 工作进程管理器未初始化或插件无 worker 时，仅缺失 worker 信息
            logger.debug(
                f"Plugin worker info unavailable for {plugin_id}: {e}",
                exc_info=True,
            )

        info["worker"] = worker_info

        return success(data=info)
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.get_detail", fallback="插件详情查询失败，请稍后重试")
        logger.error("[plugins.get_detail] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/{plugin_id}/enable", dependencies=[Depends(require_permission("plugin:config:update"))])
def enable_plugin(plugin_id: str):
    try:
        manager = get_plugin_manager()
        manager.enable_plugin(plugin_id)
        return success(data={"message": f"Plugin '{plugin_id}' enabled"})
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.enable", fallback="插件启用失败，请稍后重试")
        logger.error("[plugins.enable] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/{plugin_id}/disable", dependencies=[Depends(require_permission("plugin:config:update"))])
def disable_plugin(plugin_id: str):
    try:
        manager = get_plugin_manager()
        manager.disable_plugin(plugin_id)
        return success(data={"message": f"Plugin '{plugin_id}' disabled"})
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.disable", fallback="插件禁用失败，请稍后重试")
        logger.error("[plugins.disable] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/{plugin_id}/reload", dependencies=[Depends(require_permission("plugin:config:update"))])
def reload_plugin(plugin_id: str):
    try:
        manager = get_plugin_manager()
        manager._loader.reload_plugin(plugin_id)
        return success(data={"message": f"Plugin '{plugin_id}' reloaded"})
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.reload", fallback="插件重载失败，请稍后重试")
        logger.error("[plugins.reload] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.delete("/{plugin_id}", dependencies=[Depends(require_permission("plugin:config:update"))])
def uninstall_plugin(plugin_id: str):
    try:
        manager = get_plugin_manager()
        manager.uninstall_plugin(plugin_id)
        return success(data={"message": f"Plugin '{plugin_id}' uninstalled"})
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.uninstall", fallback="插件卸载失败，请稍后重试")
        logger.error("[plugins.uninstall] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.put("/{plugin_id}/config")
def update_plugin_config(
    plugin_id: str,
    config: dict[str, Any],
    _perm: None = Depends(require_permission("plugin:config:update")),
):
    # 修复 [B22]：原端点无认证 + 弱验证，任意未登录调用方可修改任意插件配置。
    # 通过 Depends(require_permission("plugin:config:update")) 强制认证 + 权限校验，
    # 未登录调用方将得到 401，权限不足将得到 403。
    try:
        manager = get_plugin_manager()
        manager._registry.update_config(plugin_id, config)
        return success(data={"message": f"Plugin '{plugin_id}' config updated"})
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.update_config", fallback="插件配置更新失败，请稍后重试")
        logger.error("[plugins.update_config] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/{plugin_id}/dependencies")
def get_plugin_dependencies(plugin_id: str):
    try:
        resolver = get_dependency_resolver()
        tree = resolver.get_dependency_tree(plugin_id)
        order = resolver.resolve_dependencies(plugin_id)

        return success(
            data={
                "tree": tree,
                "load_order": order,
            }
        )
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.get_dependencies", fallback="插件依赖查询失败，请稍后重试")
        logger.error("[plugins.get_dependencies] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/{plugin_id}/logs")
def get_plugin_logs(
    plugin_id: str,
    level: str | None = Query(None, description="Filter by log level"),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return success(
        data={
            "logs": [],
            "total": 0,
        }
    )


@router.get("/{plugin_id}/capabilities")
def get_plugin_capabilities(plugin_id: str):
    try:
        gatekeeper = CapabilityGatekeeper.get_instance()
        caps = gatekeeper.get_plugin_capabilities(plugin_id)

        return success(
            data={
                "capabilities": caps,
            }
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.get_capabilities", fallback="插件能力查询失败，请稍后重试")
        logger.error("[plugins.get_capabilities] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.put("/{plugin_id}/capabilities/{capability}")
def update_capability_grant(
    plugin_id: str,
    capability: str,
    file_rules: list[dict] | None = None,
    network_rules: list[dict] | None = None,
    gpu_limits: dict | None = None,
    _perm: None = Depends(require_permission("plugin:capability:manage")),
):
    # 修复 [B21]：原端点无认证，任意未登录调用方可修改插件能力授权规则。
    # 通过 Depends(require_permission("plugin:capability:manage")) 强制认证 + 权限校验，
    # 未登录调用方将得到 401，权限不足将得到 403。
    try:
        gatekeeper = CapabilityGatekeeper.get_instance()
        gatekeeper.update_grant_rules(
            plugin_id,
            capability,
            file_rules=file_rules,
            network_rules=network_rules,
            gpu_limits=gpu_limits,
        )
        return success(data={"message": f"Capability '{capability}' rules updated"})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.update_capability", fallback="插件能力规则更新失败，请稍后重试")
        logger.error("[plugins.update_capability] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/workers")
def list_workers():
    try:
        worker_mgr = PluginWorkerManager.get_instance()
        workers = worker_mgr.list_workers()
        return success(data={"workers": workers})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.list_workers", fallback="工作进程列表查询失败，请稍后重试")
        logger.error("[plugins.list_workers] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/workers/{plugin_id}/start", dependencies=[Depends(require_permission("plugin:config:update"))])
def start_worker(plugin_id: str):
    try:
        manager = get_plugin_manager()
        metadata = manager._registry.get(plugin_id)

        worker_mgr = PluginWorkerManager.get_instance()
        config = WorkerConfig(
            plugin_id=plugin_id,
            plugin_path=metadata.plugin_path,
        )
        worker_mgr.start_worker(config)

        return success(data={"message": f"Worker for '{plugin_id}' started"})
    except KeyError:
        # 注意：error() 的 message/code 必须按关键字传参（与签名对齐，避免位置错位）。
        return error(code=ErrorCode.NOT_FOUND, message=f"Plugin '{plugin_id}' not found")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.start_worker", fallback="工作进程启动失败，请稍后重试")
        logger.error("[plugins.start_worker] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/workers/{plugin_id}/stop", dependencies=[Depends(require_permission("plugin:config:update"))])
def stop_worker(plugin_id: str):
    try:
        worker_mgr = PluginWorkerManager.get_instance()
        worker_mgr.stop_worker(plugin_id)
        return success(data={"message": f"Worker for '{plugin_id}' stopped"})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.stop_worker", fallback="工作进程停止失败，请稍后重试")
        logger.error("[plugins.stop_worker] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/health")
def health_check(plugin_id: str | None = None):
    try:
        worker_mgr = PluginWorkerManager.get_instance()
        results = worker_mgr.health_check(plugin_id)
        return success(data={"health": results})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：API 端点统一收口所有未预期的异常
        # 插件操作涉及注册表/沙箱/工作进程，异常族多源
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="plugins.health_check", fallback="健康检查失败，请稍后重试")
        logger.error("[plugins.health_check] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})
