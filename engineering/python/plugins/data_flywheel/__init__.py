"""数据飞轮插件包.

实现飞轮反馈闭环：
    用户反馈采集 → 写入 IDatasetStore → 模型迭代 Workflow → 评估 → 灰度热更新

对应 core-contracts-design.md 阶段 4。
"""
