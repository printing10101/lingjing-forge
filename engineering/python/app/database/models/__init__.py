"""数据库模型定义模块。"""

# 重新导出所有模型，便于外部统一导入
from app.database.models.machining_record import Base, MachiningRecord
from app.database.models.manufacturing import (
    Material,
    Equipment,
    EquipmentAlarm,
    MaintenancePlan,
    QualityRecord,
    QualityAnomaly,
    ProductionRecord,
    WorkOrder,
    ProcessRoute,
    ProcessStep,
    Document,
)
from app.database.models.tool import Tool
from app.database.models.training_task import (
    TrainingTask,
    TaskStatusEnum,
    Role,
    Permission,
    RolePermission,
    init_db,
)
from app.database.models.workflow import (
    WorkflowRun,
    WorkflowRunNode,
)
from app.database.models.workflow_template import (
    WorkflowTemplate,
    WorkflowTemplateVersion,
)
from app.database.models.project_sync import (
    ProjectRepo,
    ProjectResourceRef,
    ProjectSyncRecord,
)
from app.database.models.resource_card import (
    DatasetReadme,
    ModelArtifact,
)
from app.database.models.project_package import (
    ProjectExport,
    ProjectImport,
)
from app.database.models.dataset import (
    Dataset,
    DatasetVersion,
    LineageRecord,
    ExperimentSnapshot,
)
from app.database.models.explainability import (
    ExplanationRecord,
    ExplanationComparison,
)

# 世界模型 + RL Agent（ADR-017 阶段 8 p8）
from app.database.models.world_model import WorldModelVersionORM
from app.database.models.rl_agent import (
    RLAgentPolicyVersionORM,
    RLAgentTrainingRunORM,
)

# 切削实测经验数据（P2-1 数据飞轮）
from app.database.models.cutting_experience import (
    Base as CuttingExperienceBase,
    CuttingExperienceRecord,
    _new_experience_id,
)

__all__ = [
    "Base",
    "MachiningRecord",
    "Material",
    "Equipment",
    "EquipmentAlarm",
    "MaintenancePlan",
    "QualityRecord",
    "QualityAnomaly",
    "ProductionRecord",
    "WorkOrder",
    "ProcessRoute",
    "ProcessStep",
    "Document",
    "Tool",
    "TrainingTask",
    "TaskStatusEnum",
    "Role",
    "Permission",
    "RolePermission",
    "init_db",
    "WorkflowRun",
    "WorkflowRunNode",
    "WorkflowTemplate",
    "WorkflowTemplateVersion",
    "Dataset",
    "DatasetVersion",
    "LineageRecord",
    "ExperimentSnapshot",
    # 资源卡片（ADR-012）
    "ModelArtifact",
    "DatasetReadme",
    # 项目导入导出（ADR-015）
    "ProjectExport",
    "ProjectImport",
    # 可解释性可视化（ADR-016 阶段 7 p7）
    "ExplanationRecord",
    "ExplanationComparison",
    # 世界模型 + RL Agent（ADR-017 阶段 8 p8）
    "WorldModelVersionORM",
    "RLAgentPolicyVersionORM",
    "RLAgentTrainingRunORM",
    # 切削实测经验数据（P2-1 数据飞轮）
    "CuttingExperienceRecord",
    "CuttingExperienceBase",
    "_new_experience_id",
]
