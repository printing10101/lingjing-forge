"""服务层共享基础设施.

提供 ``BaseSingletonService`` 基类，统一 8 个单例服务（RLAgentService /
ResourceCardService / ProjectPackageService / ProjectSyncService /
ExplainabilityService / WorldModelService / WorkflowTemplateService /
ModelRegistryService）的样板代码（单例 + 锁 + _get_session + reset）。
"""

from app.services._shared.service_base import BaseSingletonService

__all__ = ["BaseSingletonService"]
