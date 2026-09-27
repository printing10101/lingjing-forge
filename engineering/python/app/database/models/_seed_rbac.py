"""RBAC 种子数据与默认 admin 用户（从 training_task 拆出）。"""

from __future__ import annotations

import logging
import os

from app.database.models._presets import PRESET_PERMISSIONS, PRESET_ROLES
from app.database.models._rbac_models import Permission, Role, RolePermission

logger = logging.getLogger(__name__)


async def _upgrade_rbac_permissions(session) -> None:
    """幂等补全缺失的权限码并授予 admin 角色。

    场景：PRESET_PERMISSIONS 在后续版本中扩充后，已初始化的旧数据库不会自动
    获得新增权限码，导致 require_permission() 校验对 admin 也返回 403，
    端点完全不可用（比"无鉴权"更严重）。

    本函数在每次启动时被 _seed_rbac 调用（当 roles 已存在时），幂等地：
    1. 补全 Permission 表中缺失的权限码记录；
    2. 将所有 PRESET_PERMISSIONS 权限授予 admin 角色（admin 应拥有全部权限）；
    3. 失效 RBAC 缓存，确保新权限立即生效。

    注意：engineer/operator 等角色的权限分配由运维通过管理界面调整，本函数不改动，
    避免"自动扩权"破坏最小权限原则。
    """
    from sqlalchemy import select

    try:
        # 1. 一次性加载所有已存在权限的 (code, id) 映射
        existing_rows = (await session.execute(select(Permission.code, Permission.id))).all()
        existing_map: dict[str, int] = {code: pid for code, pid in existing_rows}

        # 2. 补全缺失的 Permission 记录
        new_perm_added = False
        for pdata in PRESET_PERMISSIONS:
            if pdata["code"] not in existing_map:
                perm = Permission(
                    name=pdata["name"],
                    code=pdata["code"],
                    description=pdata["description"],
                )
                session.add(perm)
                await session.flush()
                existing_map[pdata["code"]] = int(perm.id)
                new_perm_added = True

        # 3. 查询 admin 角色
        admin_role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one_or_none()
        if admin_role is None:
            # admin 角色不存在，仅提交权限补全
            if new_perm_added:
                await session.commit()
            return

        # 4. 查询 admin 已关联的权限码集合
        admin_perm_codes = (
            (
                await session.execute(
                    select(Permission.code)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.role_id == admin_role.id)
                )
            )
            .scalars()
            .all()
        )
        admin_set = set(admin_perm_codes)

        # 5. 补全 admin 缺失的权限关联（包括本次新增和历史遗漏）
        binding_added = False
        for pdata in PRESET_PERMISSIONS:
            pcode = pdata["code"]
            if pcode in admin_set:
                continue
            pid = existing_map.get(pcode)
            if pid is None:
                # 理论上不会发生（前文已补全），防御性跳过
                continue
            session.add(RolePermission(role_id=admin_role.id, permission_id=pid))
            admin_set.add(pcode)
            binding_added = True

        if new_perm_added or binding_added:
            await session.commit()
            # 失效 RBAC 缓存，确保新权限立即生效
            try:
                from app.auth.permissions import rbac_cache

                rbac_cache.invalidate()
            except Exception as e:
                # P0-5 修复：缓存失效失败不得静默吞没，否则新权限不生效且无任何
                # 可观测信号，导致管理员误以为权限已生效而实际仍被旧缓存拦截。
                # 记录 warning 以便运维介入排查（常见原因：rbac_cache 模块未初始化）。
                logger.warning(
                    "RBAC cache invalidation failed after permission upgrade: %s",
                    e,
                    exc_info=True,
                )
    except Exception:
        await session.rollback()
        raise


async def _seed_rbac(session):
    from sqlalchemy import select

    existing_roles = (await session.execute(select(Role))).scalars().all()
    if existing_roles:
        # 已初始化的数据库：幂等补全后续版本新增的权限码并授予 admin 角色，
        # 避免 PRESET_PERMISSIONS 扩充后旧库 require_permission 校验始终 403。
        await _upgrade_rbac_permissions(session)
        return

    try:
        perm_map: dict[str, int] = {}
        for pdata in PRESET_PERMISSIONS:
            perm = Permission(name=pdata["name"], code=pdata["code"], description=pdata["description"])
            session.add(perm)
            await session.flush()
            perm_map[pdata["code"]] = int(perm.id)

        for rdata in PRESET_ROLES:
            role = Role(name=rdata["name"], code=rdata["code"], description=rdata["description"])
            session.add(role)
            await session.flush()

            for pcode in rdata["permissions"]:
                pid = perm_map.get(pcode)
                if pid:
                    session.add(RolePermission(role_id=role.id, permission_id=pid))

        await session.commit()
    except Exception:
        # 中间 flush 失败时回滚，避免 session 处于不一致状态
        await session.rollback()
        raise

    # P0-4 修复：种子默认 admin 用户到 UserStore（JSON 文件），保证首次启动可登录。
    # 密码取自 LJ_ADMIN_INITIAL_PASSWORD 环境变量；未设置时生成随机 16 位密码。
    # 安全设计：首次启动随机化 + 强制改密（must_change_password=True）= 安全基线。
    await _seed_default_admin_user()


async def _seed_default_admin_user() -> None:
    """首次启动时种子默认 admin 用户到 UserStore。

    幂等：若 admin 已存在则跳过。
    密码来源：环境变量 LJ_ADMIN_INITIAL_PASSWORD。
    安全设计：首次启动随机化 + 强制改密 = 安全基线。
      - 若 LJ_ADMIN_INITIAL_PASSWORD 已设置，使用该密码；
      - 若未设置，生成随机 16 位密码并打印到 stdout（仅首次启动）；
      - 无论哪种情况，均设置 must_change_password=True，要求首次登录后立即改密。
    """
    import secrets as _secrets
    import string as _string

    from app.auth.security import hash_password
    from app.dependencies import get_user_store

    store = get_user_store()
    if store.get_user("admin") is not None:
        return

    password = os.environ.get("LJ_ADMIN_INITIAL_PASSWORD")
    if not password:
        # 未注入密码时生成随机 16 位密码（大小写字母+数字）
        alphabet = _string.ascii_letters + _string.digits
        password = "".join(_secrets.choice(alphabet) for _ in range(16))

    try:
        store.create_user("admin", hash_password(password), role="admin", must_change_password=True)
        if os.environ.get("LJ_ADMIN_INITIAL_PASSWORD"):
            logger.warning(
                "[部署可用性] 已创建默认 admin 用户（密码取自 LJ_ADMIN_INITIAL_PASSWORD）。必须立即登录并修改密码！"
            )
        else:
            # P0-13 修复：随机初始密码不得输出到 stdout（会被 shell 历史、日志采集器、
            # 容器编排系统捕获）。改为写入受限文件（owner-only），并仅记录文件路径。
            # 文件路径取自 LNN_LOG_DIR（已存在且访问受控），文件名固定便于运维查找。
            import stat as _stat

            _log_dir = os.environ.get(
                "LNN_LOG_DIR",
                str(__import__("pathlib").Path(__file__).resolve().parents[3] / "logs"),
            )
            _pw_file = __import__("pathlib").Path(_log_dir) / "admin_initial_password.txt"
            try:
                _pw_file.parent.mkdir(parents=True, exist_ok=True)
                _pw_file.write_text(
                    f"[初始化] admin 用户随机初始密码（请立即保存并登录修改，完成后删除此文件）:\n{password}\n",
                    encoding="utf-8",
                )
                # 设置仅 owner 可读写（0o600），防止其他用户读取
                _pw_file.chmod(_stat.S_IRUSR | _stat.S_IWUSR)
                logger.warning(
                    "[部署可用性] 已创建默认 admin 用户。随机初始密码已写入受限文件: %s "
                    "（权限 600，仅当前用户可读）。必须立即登录并修改密码，然后删除该文件！",
                    _pw_file,
                )
            except (OSError, IOError, PermissionError) as pw_err:
                # 文件写入失败时降级：仅记录告警，不输出密码明文
                logger.error(
                    "[部署可用性] 已创建默认 admin 用户，但密码文件写入失败: %s。"
                    "请通过 LJ_ADMIN_INITIAL_PASSWORD 环境变量重新设置密码，"
                    "或联系管理员重置。",
                    pw_err,
                    exc_info=True,
                )
    except ValueError:
        # 并发场景：已被其他进程创建
        pass
    except (OSError, IOError, PermissionError) as e:
        logger.error("[部署可用性] 创建默认 admin 用户失败: %s", e, exc_info=True)
