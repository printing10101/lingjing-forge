"""Agent 状态 Repository（V3.0 Repository 层）。"""

from __future__ import annotations

from app.state.manager import StatePersistenceManager


class AgentStateRepo:
    def __init__(self, persistence: StatePersistenceManager):
        self._p = persistence

    async def save(self, state, trigger: str = ""):
        await self._p.save_state(state, trigger=trigger)

    async def load(self, agent_id: str):
        return await self._p.load_state(agent_id)


def get_state_persistence() -> StatePersistenceManager:
    """构造状态持久化管理器。

    ``StatePersistenceManager`` 需要数据库连接参数（redis_client /
    db_session_factory），无全局默认单例；请显式构造后注入。
    """
    raise NotImplementedError(
        "StatePersistenceManager 需要数据库连接参数，请使用 "
        "app.state.manager.StatePersistenceManager(...) 直接构造后传入 AgentStateRepo"
    )


def get_agent_state_repo() -> AgentStateRepo:
    return AgentStateRepo(get_state_persistence())
