"""基准脚本独立运行引导。

``performance/*.py`` 既作为 ``app.benchmarks.performance`` 包成员被导入
（如 ``tests/benchmarks/run_all.py``，此时 ``app`` 已可导入，本模块为
无害空操作），也支持以脚本方式直接运行（如
``python app/benchmarks/performance/api_bench.py``，此时需要把
``engineering/python`` 加入 ``sys.path`` 才能导入 ``app`` 包）。

脚本直跑模式下由各基准文件在 ``__package__ in (None, "")`` 分支中
``import _bootstrap`` 触发本模块。
"""

from __future__ import annotations

import os
import sys

_PY_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
if _PY_ROOT not in sys.path:
    sys.path.insert(0, _PY_ROOT)
