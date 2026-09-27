"""仿真可视化常量集中管理。

将 toolpath_visualizer 中使用的颜色、figsize、DPI、字号、线宽、padding 等
可视化参数集中至此，便于统一调整与主题切换。所有值仅限 Python 侧
（matplotlib 渲染参数 + _LEGEND_HTML 注入颜色）；HTML/CSS 中的颜色由
模板自行管理。
"""

from __future__ import annotations

# 颜色（按刀具运动类型 / 状态分类）

# 运动类型颜色 —— 必须与 _LEGEND_HTML 中显示的颜色保持一致
COLOR_RAPID_MOVE = "#f44336"  # G00 快速移动
COLOR_LINEAR_MOVE = "#4caf50"  # G01 线性进给
COLOR_ARC_MOVE = "#2196f3"  # G02/G03 圆弧
COLOR_DWELL = "#ffc107"  # G04 暂停

# 未知 / 默认刀具路径段颜色
COLOR_DEFAULT_SEGMENT = "#333"

# 毛坯线框 / 表面颜色
COLOR_STOCK = "gray"

# Matplotlib 图表

FIGSIZE_DEFAULT = (12, 9)
DPI_PNG = 150

# 字号
FONT_SIZE_TITLE = 14
FONT_SIZE_LEGEND = 9

# 线宽
LINE_WIDTH_TOOLPATH = 1.5
LINE_WIDTH_STOCK_WIREFRAME = 0.5

# 子图边距 (left, right, top, bottom)
SUBPLOT_ADJUST = (0.02, 0.98, 0.95, 0.02)

# 3D 场景 / 毛坯

# 坐标轴方向 padding（mm）
STOCK_PADDING = 30
# Z 轴额外上侧 padding（mm），用于容纳刀柄/标尺
STOCK_Z_PADDING_EXTRA = 30

# 无毛坯时的默认视图范围
DEFAULT_X_LIM = (-100, 100)
DEFAULT_Y_LIM = (-100, 100)
DEFAULT_Z_LIM = (-10, 110)

# 标签 / 标题 / 图例

AXIS_LABEL_X = "X (mm)"
AXIS_LABEL_Y = "Y (mm)"
AXIS_LABEL_Z = "Z (mm)"
TITLE_TEXT = "NC Toolpath 3D Simulation Visualization"
LEGEND_LOC = "upper right"
