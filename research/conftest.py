"""research 测试套件公共路径装配。

tests/ 是常规包（含 __init__.py），pytest 导入测试模块时会把 research/
放进 sys.path，`training`、`models`、`quantization` 等本侧顶层包因此可导入。
但 test_quantization / test_gpu_training / test_model_benchmark /
test_data_split 还要 `from app...` 导入工程侧包，而 app 在 engineering/python
下——过去靠桌面宿主注入的 PYTHONPATH 碰巧通过（AGENTS.md 坑 1 的反面），
2026-09 仓库重建后在干净 shell 里这 4 个文件收集失败。

这里统一补路径，规矩是"存在才加、append 不 prepend"：research 自己的
顶层包必须优先于工程侧同名物，避免复现坑 1 式的包遮蔽。
"""

import sys
from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parent
ENGINEERING_PY = RESEARCH_ROOT.parent / "engineering" / "python"

if str(RESEARCH_ROOT) not in sys.path:
    sys.path.append(str(RESEARCH_ROOT))

if ENGINEERING_PY.is_dir() and str(ENGINEERING_PY) not in sys.path:
    sys.path.append(str(ENGINEERING_PY))
