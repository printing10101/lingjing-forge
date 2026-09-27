"""数据飞轮插件工作流模板包.

包含模型迭代管线（model_iteration_pipeline）的 YAML 模板，
由 ``plugins.data_flywheel.main.Plugin._load_model_iteration_spec`` 加载
并转换为 :class:`WorkflowSpec`。

模板列表：
    - model_iteration_pipeline.yaml: 反馈采集 → 训练 → 评估 → 注册 → 灰度热更新
"""
