"""领域路由注册聚合层.

设计目标：
- ``app/api/v1/`` 下的 50+ 扁平 ``.py`` 文件保持物理位置不变（向后兼容）
- 本包按业务领域聚合注册逻辑，每个领域一个注册模块
- ``app/router_registry.py`` 仅调用本包的领域注册函数，不再直接 ``include_router``

领域划分（12 个领域）：
1. system         - 系统域（health / status）
2. identity       - 身份域（auth / users / user_sovereignty）
3. ai             - AI 域（LNN / RAG / Ollama / LLM Provider / SHARP / 工艺理解 / 动态调参 / 信号融合 / 知识图谱）
4. tasks          - 任务域（jobs / agent_state / agent_gateway / task_checkout / heartbeat）
5. governance     - 治理域（skills / cost_budget / governance / goal_alignment）
6. manufacturing  - 制造域（materials / equipment / quality / production / process / documents）
7. engineering    - 工程域（simulation / chatter / cutting_force / collision / tools / project / step / rules / dxf / nl2cad）
8. dnc_mes        - 通信域（DNC 机床通信 / MES 集成）
9. templates      - 模板域（template_* / pattern_engine / flywheel / template_market）
10. workflows     - 工作流域（ADR-005/010/011/012/015/016/017 全部产物，含 explainability / world_model / rl_agent）
11. plugins       - 插件域
12. adr_pipeline  - ADR 阶段 1-7 条件模块链路（拍照重建 → 特征提取 → 参数化 → 切削参数 → 颤振预测 → G 代码 → CAM 校验）

注册顺序约定：
- system 最先注册（健康检查端点必须先于业务路由）
- identity 次之（认证依赖系统端点）
- 业务域按依赖顺序注册
- adr_pipeline 最后注册（依赖可选库，失败仅告警）

条件标志位传递：
- ``register_all_domain_routers`` 接受 ``ollama_available`` 等条件标志位
- 标志位由 ``router_registry.py`` 集中定义，通过参数传递给各领域注册函数
- 避免领域模块反向依赖 ``router_registry`` 造成循环导入
"""

from __future__ import annotations

from fastapi import FastAPI

from . import (
    adr_pipeline,
    ai,
    dnc_mes,
    engineering,
    governance,
    identity,
    manufacturing,
    plugins,
    system,
    tasks,
    templates,
    workflows,
)

__all__ = ["register_all_domain_routers"]


def register_all_domain_routers(
    app: FastAPI,
    *,
    ollama_available: bool = False,
) -> dict[str, bool]:
    """按领域顺序注册所有路由.

    注册顺序与 ``router_registry.register_routers`` 原始实现保持一致，
    确保：
    - 健康检查端点优先注册
    - 业务路由按域分组
    - 条件路由（依赖可选库）最后注册，失败仅告警不阻断启动

    Args:
        app: FastAPI 应用实例
        ollama_available: Ollama 模块是否可用（由 router_registry 传入）

    Returns:
        ADR 阶段 1-7 条件模块的导入可用状态字典（键名与
        ``router_registry`` 全局变量同名），由调用方写入全局变量
        供测试与外部观察使用
    """
    # 1. 系统域（健康检查 / 状态 / SSE）
    system.register(app)

    # 2. 身份域（认证 / 用户 / 用户主权）
    identity.register(app)

    # 3. AI 域（LNN / RAG / Ollama / LLM / SHARP / 工艺理解）
    ai.register(app, ollama_available=ollama_available)

    # 4. 任务域（jobs / agent_state / agent_gateway / task_checkout / heartbeat）
    tasks.register(app)

    # 5. 治理域（skills / cost / governance / goal）
    governance.register(app)

    # 5b. 自进化域（演化循环：统计/提案/门控发布，自进化 M1）
    from app.api.routers import evolution as evolution_domain

    evolution_domain.register(app)

    # 6. 制造域（materials / equipment / quality / production / process / documents）
    manufacturing.register(app)

    # 7. 工程域（simulation / chatter / cutting_force / collision / tools / project / step / rules / dxf / nl2cad）
    engineering.register(app)

    # 8. 通信域（DNC / MES）
    dnc_mes.register(app)

    # 9. 模板域
    templates.register(app)

    # 10. 工作流域（ADR-005/010/011/012/015/016/017）
    workflows.register(app)

    # 11. 插件域
    plugins.register(app)

    # 12. ADR 阶段 1-7 条件模块链路（最后注册，依赖可选库）
    # 返回各阶段模块的导入可用状态字典，供 router_registry 写入全局变量
    flags = adr_pipeline.register(app)
    return flags


from app.api.routers._route_registry import (
    RouterSpec,
    validate_specs,
    register_routers,
    is_duplicate_registration,
    group_by_domain,
)
