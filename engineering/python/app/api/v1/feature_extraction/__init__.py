"""几何特征辅助提取模块 API 路由。

端点设计
========
- POST /api/v1/feature_extraction/tasks
    创建特征提取任务（上传 mesh 文件 或 传入已有 mesh 路径 + 关联重建任务 ID）
- POST /api/v1/feature_extraction/tasks/{task_id}/run
    异步触发特征提取执行（平面 RANSAC + 圆柱拟合 + 孔检测）
- GET  /api/v1/feature_extraction/tasks/{task_id}
    查询任务状态（含 feature_disclaimer）
- GET  /api/v1/feature_extraction/tasks
    列出最近任务
- GET  /api/v1/feature_extraction/tasks/{task_id}/result
    获取已提取的特征列表（FEATURES_EXTRACTED 状态后可调用）
- POST /api/v1/feature_extraction/tasks/{task_id}/review
    工程师审核单个特征（confirmed / rejected / edited）—— 人工介入核心端点
- GET  /api/v1/feature_extraction/tasks/{task_id}/export
    导出已确认特征集为 JSON 文件（供阶段 3 参数化 STEP 生成使用）
- DELETE /api/v1/feature_extraction/tasks/{task_id}
    删除任务（清理 workspace）
- GET  /api/v1/feature_extraction/precision_info
    查询精度档位信息与工业硬门槛（不创建任务）

权限：feature_extraction:read（参考 image_to_3d:read 风格）

设计原则（项目记忆硬约束）
--------
- mesh → 参数化 CAD 自动转换工业上未解决，本模块输出「算法建议特征」
- 工程师必须逐条审核（confirmed / rejected / edited）后才允许进入阶段 3
- 所有响应携带 feature_disclaimer 字段，明确告知精度限制与硬门槛
- 生成的特征集仅供阶段 3 参数化 STEP 生成参考，
  生成的 STEP / G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
"""

from app.api.v1.feature_extraction.routes import router

__all__ = ["router"]
