"""参数化几何输出模块 API 路由。

端点设计
========
- POST /api/v1/parametric_geometry/tasks
    创建参数化几何任务（输入阶段 2 confirmed_features.json 路径 + 关联特征提取任务 ID）
- POST /api/v1/parametric_geometry/tasks/{task_id}/run
    异步触发流水线执行（特征→B-rep→装配→STEP）
- GET  /api/v1/parametric_geometry/tasks/{task_id}
    查询任务状态（含 step_disclaimer）
- GET  /api/v1/parametric_geometry/tasks
    列出最近任务
- GET  /api/v1/parametric_geometry/tasks/{task_id}/result
    获取 STEP 生成结果 + 装配摘要 + brep_shapes 概览
- POST /api/v1/parametric_geometry/tasks/{task_id}/review
    工程师审核单个特征在 STEP 中的表达（confirmed / rejected / edited）—— 第一轮审核
- POST /api/v1/parametric_geometry/tasks/{task_id}/finalize
    基于审核结果重新生成最终 STEP —— 第二轮 STEP 生成
- GET  /api/v1/parametric_geometry/tasks/{task_id}/step/download
    下载 STEP 文件（STEP_GENERATED 状态下载初版，SUCCEEDED 状态下载最终版）
- DELETE /api/v1/parametric_geometry/tasks/{task_id}
    取消/删除任务
- GET  /api/v1/parametric_geometry/precision_info
    查询精度档位信息与工业硬门槛（不创建任务）

权限：parametric_geometry:read（参考 feature_extraction:read 风格）

设计原则（项目记忆硬约束）
--------
- mesh → 参数化 CAD 自动转换工业上未解决，本模块输出「算法建议 STEP」
- 两轮审核：第一轮（STEP_GENERATED）审核 STEP 中特征表达；第二轮基于 effective_params 重生成最终 STEP
- 即便审核通过，最终 STEP 必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
- 本系统定位为「工程师助手」，非「全自动生产线」
- 所有响应携带 step_disclaimer 字段，明确告知精度限制与硬门槛
"""

from app.api.v1.parametric_geometry.routes import router

__all__ = ["router"]
