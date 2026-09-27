"""拍照重建模块 API 路由。

端点设计
========
- POST /api/v1/image_to_3d/tasks
    创建重建任务（接收多张照片 + 可选标定块距离），返回 task_id
- POST /api/v1/image_to_3d/tasks/{task_id}/run
    异步触发重建执行
- GET  /api/v1/image_to_3d/tasks/{task_id}
    查询任务状态（含 precision_disclaimer）
- GET  /api/v1/image_to_3d/tasks/{task_id}/result
    下载最终 mesh 文件
- GET  /api/v1/image_to_3d/tasks
    列出最近任务
- DELETE /api/v1/image_to_3d/tasks/{task_id}
    删除任务（清理 workspace）
- GET  /api/v1/image_to_3d/precision_info
    查询当前精度档位信息（不创建任务）

权限：image_to_3d:read（参考 step:read 风格）
"""
