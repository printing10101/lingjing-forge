"""``python -m lomo`` 入口。

等价于 ``lomo`` console script（若已通过 pip install -e . 安装）。
"""

from lomo.cli import main

if __name__ == "__main__":
    main()
