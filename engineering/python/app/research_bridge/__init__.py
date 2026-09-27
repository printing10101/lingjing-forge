"""研究桥接模块（E-16 调查结论：非孤儿模块，合法保留）。

定位：
- 产品-研究之间的单向数据/能力桥梁，将生产环境中的问题、数据、实验反馈
  匿名化后输送给研究侧（论文/实验/模型迭代），不引入研究代码到生产路径。
- 被 ``app/api/v1/status.py``（研究桥接状态上报）和
  ``app/dxf/process_service.py``（DXF 处理反馈）引用，属合法产品依赖。

子模块：
- data_collector / data_anonymizer : 采集并匿名化生产数据
- feature_flags : 研究特性开关
"""

# 包级 re-export：status.py 等消费方从包根导入（延迟导入避免循环）
from .data_collector import UsageDataCollector
