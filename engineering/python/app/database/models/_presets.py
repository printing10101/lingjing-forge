"""RBAC 预设权限码与预设角色（从 training_task 拆出）。"""

from __future__ import annotations


# 权限码体系统一原则：
# 权限码格式: <module>:<action>，必须与各 API 路由中 require_permission() 调用的
# 权限码完全一致，否则 RBAC 校验会因权限码不在 PRESET_PERMISSIONS 中而始终失败。
# 新增 API 端点时，若引入新的 require_permission 码，必须同步追加到此处。
PRESET_PERMISSIONS = [
    # 原有 12 个权限码
    {"code": "system:config", "name": "系统配置管理", "description": "修改系统全局配置参数"},
    {"code": "llm:chat", "name": "LLM对话", "description": "调用 LLM 流式/非流式对话补全"},
    {"code": "user:manage", "name": "用户管理", "description": "查看、创建、修改、禁用用户账号"},
    {"code": "project:create", "name": "项目创建", "description": "创建新的加工项目"},
    {"code": "project:delete", "name": "项目删除", "description": "删除已有加工项目"},
    {"code": "simulation:run", "name": "仿真运行", "description": "运行刀具路径仿真"},
    {"code": "simulation:configure", "name": "仿真配置", "description": "修改仿真参数配置"},
    {"code": "result:view", "name": "结果查看", "description": "查看仿真和分析结果"},
    {"code": "report:export", "name": "报告导出", "description": "导出加工和仿真报告"},
    {"code": "model:train", "name": "模型训练", "description": "训练LNN预测模型"},
    {"code": "model:predict", "name": "模型预测", "description": "使用模型进行预测推理"},
    {"code": "rule:edit", "name": "规则编辑", "description": "编辑加工规则"},
    {"code": "toolpath:edit", "name": "刀路编辑", "description": "编辑刀具路径"},
    # 补充：与 API 路由 require_permission() 调用对齐的缺失权限码
    # LNN 模型模块
    {"code": "lnn:read", "name": "LNN查询", "description": "查询LNN模型列表、预测结果、任务状态"},
    {"code": "lnn:write", "name": "LNN写入", "description": "执行LNN预测、保存结果、清理缓存、模型量化"},
    {"code": "lnn:train", "name": "LNN训练", "description": "发起LNN模型训练及训练试运行"},
    # 材料库
    {"code": "materials:read", "name": "材料库查询", "description": "查询材料库数据"},
    # 设备管理
    {"code": "equipment:read", "name": "设备查询", "description": "查询设备信息与状态"},
    # 刀具磨损预测
    {"code": "wear:read", "name": "磨损预测查询", "description": "查询刀具磨损预测结果"},
    # 用户信息
    {"code": "user:read", "name": "用户信息查看", "description": "查看当前用户自身信息与权限"},
    # 刀具管理
    {"code": "tools:read", "name": "刀具库查询", "description": "查询刀具库数据"},
    # 模板市场
    {"code": "template:read", "name": "模板市场查询", "description": "查询模板市场数据"},
    # 任务领取
    {"code": "task:checkout:read", "name": "任务领取查询", "description": "查询可领取的任务及锁状态"},
    {"code": "task:checkout:write", "name": "任务领取操作", "description": "领取、完成、放弃任务及队列操作"},
    {"code": "task:lock:release", "name": "任务锁释放", "description": "释放任务锁"},
    # 信号融合知识库
    {"code": "signal_kb:read", "name": "信号知识库查询", "description": "查询信号融合知识库样本与检索"},
    {"code": "signal_kb:write", "name": "信号知识库写入", "description": "新增、批量导入、删除信号知识库样本"},
    # SHARP 轨迹
    {"code": "sharp:read", "name": "SHARP查询", "description": "查询SHARP轨迹与状态"},
    {"code": "sharp:write", "name": "SHARP写入", "description": "写入SHARP轨迹数据"},
    # 生产管理
    {"code": "production:read", "name": "生产管理查询", "description": "查询生产记录与工单"},
    # 工艺路线
    {"code": "process:read", "name": "工艺路线查询", "description": "查询工艺路线数据"},
    # 工艺解释器
    {"code": "explainer:read", "name": "工艺解释查询", "description": "查询工艺解释与对话"},
    {"code": "explainer:write", "name": "工艺解释写入", "description": "创建、删除工艺解释会话及清理"},
    # 插件管理
    {"code": "plugin:config:update", "name": "插件配置更新", "description": "更新插件配置"},
    {"code": "plugin:capability:manage", "name": "插件能力管理", "description": "管理插件能力注册"},
    # 模式引擎
    {"code": "pattern:read", "name": "模式引擎查询", "description": "查询加工模式识别结果"},
    # STEP 导入
    {"code": "step:read", "name": "STEP导入查询", "description": "查询STEP导入数据"},
    # 飞轮指标
    {"code": "flywheel:read", "name": "飞轮指标查询", "description": "查询飞轮指标数据"},
    # 成本预算
    {"code": "cost:budget", "name": "成本预算", "description": "成本预算查询与管理"},
    # 动态调整
    {"code": "adjust:read", "name": "动态调整查询", "description": "查询动态调整决策"},
    {"code": "adjust:write", "name": "动态调整写入", "description": "执行NC重写、闭环控制、磨损校准"},
    # DXF 管线
    {"code": "dxf:read", "name": "DXF管线查询", "description": "查询DXF管线数据"},
    # Agent 状态
    {"code": "agents:read", "name": "Agent状态查询", "description": "查询Agent状态与记忆"},
    {"code": "agents:write", "name": "Agent状态写入", "description": "修改Agent状态、记忆与检查点"},
    {"code": "agents:admin", "name": "Agent管理", "description": "Agent高级管理操作（重置、导出等）"},
    # 第二轮复查补全：与 API 路由 require_permission() 调用对齐的缺失权限码
    # 目标对齐
    {"code": "goal:read", "name": "目标对齐查询", "description": "查询目标对齐数据与任务状态"},
    {"code": "goal:write", "name": "目标对齐写入", "description": "创建、更新、删除目标与任务及状态推进"},
    # 文档管理
    {"code": "documents:read", "name": "文档查询", "description": "查询文档库数据"},
    # DNC 通信
    {"code": "dnc:read", "name": "DNC查询", "description": "查询DNC通信数据"},
    # 碰撞检测
    {"code": "collision:check", "name": "碰撞检测", "description": "执行刀路碰撞检测"},
    # Agent 网关（单数命名，与 agent_gateway.py 对齐）
    {"code": "agent:read", "name": "Agent网关查询", "description": "查询Agent网关状态、训练流、审计"},
    {"code": "agent:predict", "name": "Agent预测", "description": "Agent预测推理"},
    {"code": "agent:train", "name": "Agent训练", "description": "发起Agent训练任务"},
    {"code": "agent:execute", "name": "Agent执行", "description": "Agent执行操作"},
    {"code": "agent:audit:read", "name": "Agent审计查询", "description": "查询Agent审计日志"},
    {"code": "agent:token:create", "name": "Agent令牌创建", "description": "创建Agent访问令牌"},
    {"code": "agent:token:revoke", "name": "Agent令牌撤销", "description": "撤销单个Agent访问令牌"},
    {"code": "agent:token:revoke_all", "name": "Agent令牌全部撤销", "description": "撤销全部Agent访问令牌"},
    # 规则写入（与已注册的 rule:edit 并存，rules/api.py 使用 rule:write）
    {"code": "rule:write", "name": "规则写入", "description": "创建、更新、删除加工规则及分组、导入、备份"},
    # 备份
    {"code": "backup:read", "name": "备份查询", "description": "查询与导出规则备份"},
    # 作业管理
    {"code": "job:read", "name": "作业查询", "description": "查询作业列表与状态"},
    {"code": "job:manage", "name": "作业管理", "description": "管理作业执行与配置"},
    # 技能市场
    {"code": "skills:read", "name": "技能查询", "description": "查询技能列表与市场"},
    {"code": "skills:write", "name": "技能写入", "description": "创建、更新、删除、导入导出技能及市场操作"},
    # 心跳调度
    {"code": "heartbeat:read", "name": "心跳调度查询", "description": "查询调度任务、预算与统计"},
    {"code": "heartbeat:write", "name": "心跳调度写入", "description": "创建、触发、暂停、恢复、删除调度任务"},
    # 插件管理（路由级读权限）
    {"code": "plugin:read", "name": "插件查询", "description": "查询插件列表与状态"},
    # NL2CAD
    {"code": "nl2cad:read", "name": "NL2CAD查询", "description": "查询自然语言转CAD数据"},
    # 模板分支
    {"code": "template-branch:read", "name": "模板分支查询", "description": "查询模板分支数据"},
    {"code": "template-branch:write", "name": "模板分支写入", "description": "创建、合并、删除模板分支"},
    # 模板 A/B 测试
    {"code": "template-abtest:read", "name": "模板A/B测试查询", "description": "查询A/B测试实验"},
    {"code": "template-abtest:write", "name": "模板A/B测试写入", "description": "创建、记录、评估、终止A/B测试"},
    # 模板演进
    {"code": "template-evolution:read", "name": "模板演进查询", "description": "查询演进建议与历史"},
    {"code": "template-evolution:write", "name": "模板演进写入", "description": "创建、应用演进建议及触发评估"},
    # 模板更新
    {"code": "template-update:read", "name": "模板更新查询", "description": "查询模板更新通知"},
    {"code": "template-update:write", "name": "模板更新写入", "description": "扫描、应用、忽略模板更新通知"},
    # 工作流编排（ADR-005 阶段 1）
    {"code": "workflow:read", "name": "工作流查询", "description": "查询工作流规格、运行状态与节点详情"},
    {"code": "workflow:write", "name": "工作流写入", "description": "创建、启动、取消工作流及上传模板"},
    {"code": "workflow:manage", "name": "工作流管理", "description": "管理工作流模板、删除运行记录与批量操作"},
    # 数据集与血缘（ADR-005 阶段 2）
    {"code": "dataset:read", "name": "数据集查询", "description": "查询数据集、版本、血缘记录与读取内容"},
    {"code": "dataset:write", "name": "数据集写入", "description": "创建数据集、提交版本与记录血缘"},
    {"code": "dataset:manage", "name": "数据集管理", "description": "废弃数据集版本与批量管理操作"},
    # 实验快照与可观测（ADR-005 阶段 2）
    {"code": "snapshot:read", "name": "实验快照查询", "description": "查询实验快照、git 信息与指标"},
    {"code": "snapshot:write", "name": "实验快照创建", "description": "创建实验快照，自动采集 git 与环境"},
    {"code": "snapshot:reproduce", "name": "实验快照复现", "description": "触发快照一键复现工作流"},
    # 资源卡片（ADR-012 阶段 6 p6-3）
    {
        "code": "resource_card:read",
        "name": "资源卡片查询",
        "description": "查询模型卡片、数据集 README、卡片聚合与 lineage 摘要",
    },
    {
        "code": "resource_card:write",
        "name": "资源卡片写入",
        "description": "注册 / 更新 / 删除模型产物、追加指标、upsert 数据集 README",
    },
    # 项目导入导出（ADR-015 阶段 6 p6-4）
    {
        "code": "project_package:read",
        "name": "项目包查询",
        "description": "查询导出/导入记录、校验包、预览导入、下载 .lomo 包",
    },
    {
        "code": "project_package:write",
        "name": "项目包写入",
        "description": "导出项目为 .lomo 包、导入 .lomo 包、删除导出记录",
    },
    # 可解释性可视化（ADR-016 阶段 7 p7）
    {"code": "explainability:read", "name": "可解释性查询", "description": "查询历史解释记录、解释详情、对比解释"},
    {
        "code": "explainability:write",
        "name": "可解释性生成",
        "description": "生成隐状态投影/门控动力学/反事实/置信度解释、删除解释记录",
    },
    # 世界模型 + RL Agent（ADR-017 阶段 8 p8）
    {"code": "world_model:read", "name": "世界模型查询", "description": "查询世界模型版本列表、版本详情"},
    {"code": "world_model:write", "name": "世界模型预测", "description": "执行世界模型轨迹预测（不走工作流）"},
    {"code": "rl_agent:read", "name": "RL策略查询", "description": "查询RL策略版本、版本详情、训练状态"},
    {"code": "rl_agent:write", "name": "RL决策与训练", "description": "执行RL决策、启动训练Workflow、停止训练"},
    # CAM 校验（ADR-018 阶段 7：G 代码InternalValidatorCamAdapter审核CAM 校验报告）
    {
        "code": "cam_validation:read",
        "name": "CAM校验查询",
        "description": "查询 CAM 校验任务状态、结果列表、precision_info",
    },
    {"code": "cam_validation:create", "name": "CAM校验创建", "description": "创建 CAM 校验任务（PENDING）"},
    {
        "code": "cam_validation:run",
        "name": "CAM校验执行",
        "description": "触发 CAM 校验流水线（PENDING → RUNNING → VALIDATED）",
    },
    {
        "code": "cam_validation:review",
        "name": "CAM校验审核",
        "description": "工程师审核单个特征校验结果（VALIDATED → REVIEWED）",
    },
    {
        "code": "cam_validation:confirm",
        "name": "CAM校验确认",
        "description": "确认任务并导出 cam_report.json（REVIEWED → SUCCEEDED）",
    },
    {
        "code": "cam_validation:download",
        "name": "CAM报告下载",
        "description": "下载 cam_report.json / internal_report.json",
    },
    {"code": "cam_validation:delete", "name": "CAM校验删除", "description": "取消/删除任务（SUCCEEDED 禁删）"},
    # --- 第三轮复查补全（2026-08-23 发布前全量审计）---
    # 依据：审计脚本扫描全部 require_permission("xxx") 调用，与预设/角色权限对比，
    # 补齐此前新模块遗漏登记的权限码，避免 RBAC 因权限码未注册而始终 403。
    # 图纸3D工艺NC 核心链路模块：
    {"code": "image_to_3d:read", "name": "图像转3D查询", "description": "查询图像转3D任务状态与结果"},
    {"code": "parametric_geometry:read", "name": "参数化几何查询", "description": "查询参数化几何任务状态与结果"},
    {"code": "feature_extraction:read", "name": "特征提取查询", "description": "查询特征提取任务状态与结果"},
    {"code": "gcode_generation:read", "name": "G代码生成查询", "description": "查询G代码生成任务状态与结果"},
    {"code": "cutting_parameters:read", "name": "切削参数查询", "description": "查询切削参数任务状态与结果"},
    {"code": "chatter_prediction:read", "name": "颤振预测查询", "description": "查询颤振预测任务状态与结果"},
    {"code": "chatter:write", "name": "颤振分析写入", "description": "执行颤振SLD/模态/预测分析"},
    {"code": "postprocessor:read", "name": "后处理器查询", "description": "查询后处理器方言与配置"},
    {"code": "project:read", "name": "项目查询", "description": "查询项目列表与详情"},
    {"code": "project:write", "name": "项目写入", "description": "创建、更新项目"},
    {"code": "project_sync:write", "name": "项目同步写入", "description": "执行项目同步操作"},
    {"code": "project_sync:delete", "name": "项目同步删除", "description": "删除项目同步记录"},
    # 知识库 / 治理 / 运维：
    {"code": "kg:read", "name": "知识图谱查询", "description": "查询知识图谱节点、边、统计"},
    {"code": "rag:write", "name": "RAG知识库写入", "description": "增删RAG文档、导入、维护、工艺检索写入"},
    {"code": "experience:read", "name": "经验库查询", "description": "查询经验库数据"},
    {"code": "experience:write", "name": "经验库写入", "description": "写入经验库数据"},
    {"code": "mes:read", "name": "MES集成查询", "description": "查询MES集成数据与状态"},
    {"code": "monitor:read", "name": "运行监控查询", "description": "订阅系统运行监控与WS事件"},
    {"code": "optimizer:read", "name": "优化器查询", "description": "查询优化器任务与结果"},
    {"code": "optimizer:write", "name": "优化器写入", "description": "执行优化任务"},
    {"code": "governance:read", "name": "治理查询", "description": "查询治理规则与记录"},
    {"code": "governance:write", "name": "治理写入", "description": "创建、更新治理规则"},
    {"code": "governance:emergency", "name": "治理应急操作", "description": "执行治理应急操作"},
    {"code": "user:write", "name": "用户写入", "description": "修改用户审计日志等数据"},
    {"code": "backup:export", "name": "备份导出", "description": "导出知识库备份"},
    {"code": "backup:import", "name": "备份导入", "description": "导入知识库备份"},
    # 工作流模板：
    {"code": "workflow_template:publish", "name": "工作流模板发布", "description": "发布工作流模板"},
    {"code": "workflow_template:rate", "name": "工作流模板评分", "description": "对工作流模板评分"},
    {"code": "workflow_template:manage", "name": "工作流模板管理", "description": "管理工作流模板"},
]

PRESET_ROLES = [
    {
        "code": "admin",
        "name": "管理员",
        "description": "系统管理员，拥有全部操作权限",
        "permissions": [
            # 原有 12 码
            "system:config",
            "user:manage",
            "project:create",
            "project:delete",
            "simulation:run",
            "simulation:configure",
            "result:view",
            "report:export",
            "model:train",
            "model:predict",
            "llm:chat",
            "rule:edit",
            "toolpath:edit",
            # 补充的全部新增权限码（管理员拥有全部权限）
            "lnn:read",
            "lnn:write",
            "lnn:train",
            "materials:read",
            "equipment:read",
            "wear:read",
            "user:read",
            "tools:read",
            "template:read",
            "task:checkout:read",
            "task:checkout:write",
            "task:lock:release",
            "signal_kb:read",
            "signal_kb:write",
            "sharp:read",
            "sharp:write",
            "production:read",
            "process:read",
            "explainer:read",
            "explainer:write",
            "plugin:config:update",
            "plugin:capability:manage",
            "pattern:read",
            "step:read",
            "flywheel:read",
            "cost:budget",
            "adjust:read",
            "adjust:write",
            "dxf:read",
            "agents:read",
            "agents:write",
            "agents:admin",
            # 第二轮复查补全：管理员拥有全部新增权限码
            "goal:read",
            "goal:write",
            "documents:read",
            "dnc:read",
            "collision:check",
            "agent:read",
            "agent:predict",
            "agent:train",
            "agent:execute",
            "agent:audit:read",
            "agent:token:create",
            "agent:token:revoke",
            "agent:token:revoke_all",
            "rule:write",
            "backup:read",
            "job:read",
            "job:manage",
            "skills:read",
            "skills:write",
            "heartbeat:read",
            "heartbeat:write",
            "plugin:read",
            "nl2cad:read",
            "template-branch:read",
            "template-branch:write",
            "template-abtest:read",
            "template-abtest:write",
            "template-evolution:read",
            "template-evolution:write",
            "template-update:read",
            "template-update:write",
            "workflow:read",
            "workflow:write",
            "workflow:manage",
            # 阶段 2：数据集 + 快照
            "dataset:read",
            "dataset:write",
            "dataset:manage",
            "snapshot:read",
            "snapshot:write",
            "snapshot:reproduce",
            # 阶段 6 p6-3：资源卡片
            "resource_card:read",
            "resource_card:write",
            # 阶段 6 p6-4：项目导入导出
            "project_package:read",
            "project_package:write",
            # 阶段 7 p7：可解释性可视化
            "explainability:read",
            "explainability:write",
            # 阶段 8 p8：世界模型 + RL Agent（管理员拥有全部权限）
            "world_model:read",
            "world_model:write",
            "rl_agent:read",
            "rl_agent:write",
            # ADR-018 阶段 7：CAM 校验（管理员拥有全部权限）
            "cam_validation:read",
            "cam_validation:create",
            "cam_validation:run",
            "cam_validation:review",
            "cam_validation:confirm",
            "cam_validation:download",
            "cam_validation:delete",
            # 第三轮复查补全：管理员拥有全部权限码（与 PRESET_PERMISSIONS 同步）
            "image_to_3d:read",
            "parametric_geometry:read",
            "feature_extraction:read",
            "gcode_generation:read",
            "cutting_parameters:read",
            "chatter_prediction:read",
            "chatter:write",
            "postprocessor:read",
            "project:read",
            "project:write",
            "project_sync:write",
            "project_sync:delete",
            "kg:read",
            "rag:write",
            "experience:read",
            "experience:write",
            "mes:read",
            "monitor:read",
            "optimizer:read",
            "optimizer:write",
            "governance:read",
            "governance:write",
            "governance:emergency",
            "user:write",
            "backup:export",
            "backup:import",
            "workflow_template:publish",
            "workflow_template:rate",
            "workflow_template:manage",
        ],
    },
    {
        "code": "engineer",
        "name": "工程师",
        "description": "工程技术人员，具备项目创建和仿真运行权限",
        "permissions": [
            "project:create",
            "simulation:run",
            "result:view",
            "report:export",
            "model:predict",
            "llm:chat",
            "rule:edit",
            "toolpath:edit",
            "workflow:read",
            "workflow:write",
            "dataset:read",
            "dataset:write",
            "snapshot:read",
            "snapshot:write",
            # 阶段 6 p6-3：资源卡片（工程师默认读写）
            "resource_card:read",
            "resource_card:write",
            # 阶段 6 p6-4：项目导入导出（工程师默认读写）
            "project_package:read",
            "project_package:write",
            # 阶段 7 p7：可解释性可视化（工程师默认读写）
            "explainability:read",
            "explainability:write",
            # 阶段 8 p8：世界模型 + RL Agent（工程师默认读写）
            "world_model:read",
            "world_model:write",
            "rl_agent:read",
            "rl_agent:write",
            # ADR-018 阶段 7：CAM 校验（工程师默认拥有全部 CAM 校验权限）
            # 定位「工程师助手」，工程师是 CAM 校验的主要使用者
            "cam_validation:read",
            "cam_validation:create",
            "cam_validation:run",
            "cam_validation:review",
            "cam_validation:confirm",
            "cam_validation:download",
            "cam_validation:delete",
        ],
    },
    {
        "code": "operator",
        "name": "操作员",
        "description": "设备操作人员，具备结果查看和报告导出权限",
        "permissions": [
            "result:view",
            "report:export",
            "model:predict",
            "llm:chat",
        ],
    },
]
