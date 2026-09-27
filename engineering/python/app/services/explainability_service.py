"""可解释性服务 —— 向后兼容 shim.

本文件原为 1423 行单体实现，已拆分为 ``app.services.explainability`` 包：

- ``_projection``         —— PCA/t-SNE/UMAP 降维（ProjectorCache）
- ``_predictor_loader``   —— LNNPredictor LRU 缓存（PredictorLoader）
- ``_payload_store``      —— payload JSON 文件 IO（PayloadStore）
- ``_record_repo``        —— 解释记录 ORM 仓储（ExplanationRecordRepo）
- ``_analytics``          —— 10 个纯函数（采集 / 构建 / 分析 / 差异）
- ``service``             —— ExplainabilityService 单例外壳 + 工厂函数

为不破坏现有导入（``app/api/v1/explainability.py`` 仍从本模块导入），
本 shim 重新导出新包的全部 8 个公共符号。

新代码请直接使用 ``from app.services.explainability import ...``。
"""
