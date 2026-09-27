"""可解释性服务包.

从原单体 ``explainability_service.py``（1423 行）拆分为 7 个模块：

- ``_projection``         —— PCA/t-SNE/UMAP 降维（ProjectorCache）
- ``_predictor_loader``   —— LNNPredictor LRU 缓存（PredictorLoader）
- ``_payload_store``      —— payload JSON 文件 IO（PayloadStore）
- ``_record_repo``        —— 解释记录 ORM 仓储（ExplanationRecordRepo）
- ``_analytics``          —— 10 个纯函数（采集 / 构建 / 分析 / 差异）
- ``service``             —— ExplainabilityService 单例外壳 + 工厂函数

向后兼容
--------
本 ``__init__.py`` 重新导出原模块的全部 8 个公共符号，确保
``from app.services.explainability_service import ...`` 与
``from app.services.explainability import ...`` 行为完全等价。
"""

from app.dependencies import get_explainability_service
