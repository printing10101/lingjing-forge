"""单例服务基类.

为 ``app/services`` 下所有单例服务（RLAgentService / ResourceCardService /
ProjectPackageService / ProjectSyncService / ExplainabilityService /
WorldModelService / WorkflowTemplateService / ModelRegistryService）提供
统一的样板代码：

- **线程安全单例**：通过 ``__init_subclass__`` 让每个子类自动获得独立的
  ``_service_singleton`` 类属性槽位和 ``_service_lock`` 锁实例，避免不同
  服务共享同一个锁（性能瓶颈）或同一个单例槽位（互相覆盖）。
- **双重检查锁**：``get_instance()`` 使用经典的双重检查锁模式，避免每次
  调用都进入临界区。
- **测试重置**：``reset_instance()`` 供测试夹具在用例之间清理全局状态。
- **Session 默认实现**：``_get_session()`` 默认从
  ``app.database.connection.get_sessionmaker`` 获取 AsyncSession，子类可覆盖。

使用方式
--------
子类只需继承 ``BaseSingletonService``，无需手动声明 ``_service_lock`` /
``_service_singleton`` / ``get_instance`` / ``reset_instance``；模块级
工厂函数 ``get_xxx_service()`` 和 ``reset_xxx_service()`` 仍需保留（向后
兼容），但内部直接委托到 ``cls.get_instance()`` / ``cls.reset_instance()``。
"""

from __future__ import annotations

import threading
from typing import Any, ClassVar


class BaseSingletonService:
    """单例服务基类.

    线程安全保证
    ------------
    - 每个子类通过 ``__init_subclass__`` 获得自己的 ``_service_lock`` 实例
      与 ``_service_singleton`` 槽位（不会与基类或其他兄弟类共享）。
    - ``get_instance`` 采用双重检查锁，避免每次调用都进入临界区。
    - ``reset_instance`` 在锁内清理单例，避免与并发的 ``get_instance`` 竞争。

    Session 默认实现
    -----------------
    子类若需访问数据库，可直接调用 ``await self._get_session()``；若不需要
    数据库，可不调用（如 ``ModelRegistryService`` 不需要 → 但仍可继承此方法
    作为占位，调用时会抛 ``RuntimeError`` 提示数据库未配置）。
    """

    # 类属性声明：每个子类通过 __init_subclass__ 自动获得独立实例
    _service_singleton: ClassVar["BaseSingletonService" | None] = None
    _service_lock: ClassVar[threading.Lock]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """为每个子类自动分配独立的 ``_service_lock`` 与 ``_service_singleton``.

        避免不同子类共享同一个锁或同一个单例槽位（若共享，会导致获取某个
        服务单例时返回另一个服务的实例，或所有服务串行化获取单例）。

        子类若显式声明 ``_service_lock`` / ``_service_singleton``，则尊重
        子类的定义（例如子类需要自定义锁类型时）。
        """
        super().__init_subclass__(**kwargs)
        if "_service_lock" not in cls.__dict__:
            cls._service_lock = threading.Lock()
        if "_service_singleton" not in cls.__dict__:
            cls._service_singleton = None

    # 单例管理

    @classmethod
    def get_instance(cls) -> "BaseSingletonService":
        """获取单例实例（双重检查锁，线程安全）.

        Returns
        -------
        BaseSingletonService
            子类的全局唯一实例。

        Notes
        -----
        - 双重检查锁避免每次调用都进入临界区。
        - ``cls._service_lock`` 是子类自己的锁（由 ``__init_subclass__``
          自动创建），不同子类之间互不影响。
        """
        if cls._service_singleton is None:
            with cls._service_lock:
                if cls._service_singleton is None:
                    cls._service_singleton = cls()
        return cls._service_singleton

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例（仅供测试夹具调用）.

        Notes
        -----
        在锁内将 ``cls._service_singleton`` 置为 ``None``，避免与并发的
        ``get_instance`` 竞争。下一次调用 ``get_instance`` 时会重新创建。
        """
        with cls._service_lock:
            cls._service_singleton = None

    # 数据库 Session 默认实现

    async def _get_session(self):
        """获取 ``AsyncSession``（每段独立 commit）.

        默认从 ``app.database.connection.get_sessionmaker`` 获取 sessionmaker
        并返回其调用结果。子类可覆盖以实现自定义 session 来源（如使用连接池
        或读写分离的 sessionmaker）。

        Raises
        ------
        RuntimeError
            数据库未配置（``get_sessionmaker()`` 返回 ``None``）。
        """
        from app.database.connection import get_sessionmaker

        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            raise RuntimeError("数据库未配置，无法获取 session")
        return sessionmaker()


__all__ = ["BaseSingletonService"]
