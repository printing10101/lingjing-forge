"""正交切削 FEA 模型生成器（CalculiX .inp）— 预置分离缝版。

物理设置：
- 平面应变 2D 正交切削（CPE4R）
- 工件：45 钢（AISI 1045），Johnson-Cook 本构（HARDENING=JOHNSON COOK + RATE DEPENDENT）
- 刀具：硬质合金（高模量弹性近似刚体），rake=0°, clearance=7°
- 切削速度 200 m/min，未变形切屑厚度 h=0.1mm，切削宽度 b=1mm（平面应变）

切屑分离策略（绕开 CalculiX 2.23 的 *DAMAGE INITIATION+接触 校验 bug）：
切屑层与工件本体网格在 y=0.9 处物理分离（双节点），缝面用接触对连接。
刀具推挤切屑层时，缝面接触张开，切屑沿 rake 面滑出 = 简化切屑形成。
切削力由 JC 塑性变形 + 摩擦 + 惯性产生。

用法: python generate_orthogonal.py
"""

import os
import math

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orthogonal_cut.inp")


def _chunk(text):
    """把逗号分隔的数值列表按每行 ≤16 项分行（CalculiX 行条目限制）。"""
    items = [s for s in text.split(", ") if s.strip()]
    lines = []
    for i in range(0, len(items), 16):
        lines.append(", ".join(items[i : i + 16]) + "\n")
    return "".join(lines)


# 几何参数（mm, N, s 单位制）
WX, WY = 2.5, 1.0  # 工件 2.5×1.0mm
DX = DY = 0.05  # 网格尺寸（快速版 0.05，细网格版 0.03）
NX = int(WX / DX)  # 50 列
NY_WORK = 18  # 工件本体行数（y∈[0,0.9]）
CUT_ROWS = 2  # 切屑层行数（y∈[0.9,1.0]，2×0.05=0.1=h）
H_CUT = CUT_ROWS * DY  # 切屑层厚度 0.1mm = h
assert H_CUT >= 0.1

# 刀具（rake=0°, clearance=7°；刀尖在顶面下方 h=0.1 处）
# 刀具向左移动（-x）：rake 面=刀具左边界（面向左侧未切削区，竖直向上），
# clearance 面=右下方斜线（已加工区上方，后角 7°），刀具主体在 rake 面右侧。
RAKE_ANGLE = 0.0
CLEAR_ANGLE = 7.0
TOOL_X_R = 2.05  # 初始 rake 面 x = 刀尖 x（贴着切屑层右端）
TOOL_Y_TIP = 0.9  # 刀尖 y = 顶面 - 切削深度(0.1mm)
TOOL_HT = 0.7  # 刀具高度
TOOL_WD = 0.7  # 刀具宽度
TDX = 0.1  # 刀具网格
VC = 1666.0  # 切削速度 mm/s（100 m/min，降低冲击）
CUT_TIME = 1.8e-4  # 总仿真时间 s（走 0.3mm）
TOOL_STROKE = 0.3  # 刀具行程 mm
MU_FRICTION = 0.9  # 缝面摩擦系数（增强切屑层约束，防止整体滑脱）
CHIP_J0 = int((TOOL_X_R - 0.55) / DX) + 1  # 切屑层起始列（加宽，滑动全程有约束）
CHIP_J1 = round(TOOL_X_R / DX)  # 切屑层右端列（必须=rake 面 x，浮点用 round）

# 材料参数
# 45 钢 / AISI 1045 (Jaspers & Dautzenberg 2002)
E_STEEL, NU_STEEL = 210000.0, 0.3
RHO_STEEL = 7.85e-9  # t/mm³
JC_A, JC_B, JC_N = 553.1, 600.8, 0.234
JC_C, JC_M = 0.0134, 1.0
TMELT, TROOM = 1800.0, 293.0
# 硬质合金刀具
E_TOOL, NU_TOOL = 600000.0, 0.22
RHO_TOOL = 1.5e-8

# 节点/单元生成
node_id = 0
nodes = []
work_top = []  # 工件顶面节点行（y=0.9，缝面 master 用）
chip_bot = []  # 切屑层底面节点行（y=0.9，缝面 slave 用）
tool_top = []


def add_node(x, y):
    global node_id
    node_id += 1
    nodes.append((node_id, x, y))
    return node_id


# 工件网格：x∈[0,2.49], y∈[0,0.90]，30 行
work_nodes = [[0] * (NX + 1) for _ in range(NY_WORK + 1)]
for i in range(NY_WORK + 1):
    for j in range(NX + 1):
        work_nodes[i][j] = add_node(j * DX, i * DY)
work_top = work_nodes[NY_WORK]

# 切屑层网格：x∈[CHIP_J0·DX, CHIP_J1·DX]（刀尖左侧待切区），y∈[0.90,1.00]，独立节点
chip_nodes = [[0] * (NX + 1) for _ in range(CUT_ROWS + 1)]
for i in range(CUT_ROWS + 1):
    for j in range(CHIP_J0, CHIP_J1 + 1):
        chip_nodes[i][j] = add_node(j * DX, 0.90 + i * DY)
chip_bot = chip_nodes[0]

# 刀具网格：rake 面在左边界（x=TOOL_X_R），向右延伸，clearance 斜线向右上翘
ncol = int(TOOL_WD / TDX) + 1  # 8 列
nrow = int(TOOL_HT / TDX) + 1  # 8 行
tan7 = math.tan(math.radians(CLEAR_ANGLE))
tool_nodes = [[0] * ncol for _ in range(nrow)]
for k in range(ncol):  # k=0 在 rake 面（x=TOOL_X_R），k 增大 x 增大
    x = TOOL_X_R + k * TDX
    y_bottom = TOOL_Y_TIP + (x - TOOL_X_R) * tan7
    for r in range(nrow):
        tool_nodes[r][k] = add_node(x, y_bottom + r * TDX)
tool_top = [tool_nodes[nrow - 1][k] for k in range(ncol)]

# 单元
el_id = 0
work_elems, chip_elems, tool_elems = [], [], []


def add_elem(eset, n1, n2, n3, n4):
    global el_id
    el_id += 1
    if eset == "WORK":
        work_elems.append((el_id, n1, n2, n3, n4))
    elif eset == "CUTLAYER":
        chip_elems.append((el_id, n1, n2, n3, n4))
    else:
        tool_elems.append((el_id, n1, n2, n3, n4))
    return el_id


for i in range(NY_WORK):
    for j in range(NX):
        a = work_nodes[i][j]
        b = work_nodes[i][j + 1]
        c = work_nodes[i + 1][j + 1]
        d = work_nodes[i + 1][j]
        add_elem("WORK", a, b, c, d)
for i in range(CUT_ROWS):
    for j in range(CHIP_J0, CHIP_J1):
        a = chip_nodes[i][j]
        b = chip_nodes[i][j + 1]
        c = chip_nodes[i + 1][j + 1]
        d = chip_nodes[i + 1][j]
        add_elem("CUTLAYER", a, b, c, d)
for r in range(nrow - 1):
    for k in range(ncol - 1):
        # k 增大 x 增大，逆时针: (r,k)(r,k+1)(r+1,k+1)(r+1,k)
        a = tool_nodes[r][k]
        b = tool_nodes[r][k + 1]
        c = tool_nodes[r + 1][k + 1]
        d = tool_nodes[r + 1][k]
        add_elem("TOOLE", a, b, c, d)

work_start = 1
chip_start = work_start + len(work_elems) + 1
tool_start = chip_start + len(chip_elems) + 1

with open(OUT, "w", encoding="utf-8") as f:
    f.write("*NODE\n")
    for nid, x, y in nodes:
        f.write(f"{nid}, {x}, {y}, 0.\n")

    f.write("*ELEMENT, TYPE=CPE4R, ELSET=WORK\n")
    for eid, n1, n2, n3, n4 in work_elems:
        f.write(f"{eid}, {n1}, {n2}, {n3}, {n4}\n")
    f.write("*ELEMENT, TYPE=CPE4R, ELSET=CUTLAYER\n")
    for eid, n1, n2, n3, n4 in chip_elems:
        f.write(f"{eid}, {n1}, {n2}, {n3}, {n4}\n")
    f.write("*ELEMENT, TYPE=CPE4R, ELSET=TOOLE\n")
    for eid, n1, n2, n3, n4 in tool_elems:
        f.write(f"{eid}, {n1}, {n2}, {n3}, {n4}\n")

    # 材料（JC 塑性，无损伤卡——绕开 2.23 校验 bug）
    f.write("*MATERIAL, NAME=STEEL45\n")
    f.write("*ELASTIC\n")
    f.write(f"{E_STEEL}, {NU_STEEL}\n")
    f.write("*PLASTIC, HARDENING=JOHNSON COOK\n")
    f.write(f"{JC_A}, {JC_B}, {JC_N}, {JC_M}, {TMELT}, {TROOM}\n")
    f.write("*RATE DEPENDENT, TYPE=JOHNSON COOK\n")
    f.write(f"{JC_C}, 1.\n")
    f.write("*DENSITY\n")
    f.write(f"{RHO_STEEL}\n")
    f.write("*MATERIAL, NAME=TOOLMAT\n")
    f.write("*ELASTIC\n")
    f.write(f"{E_TOOL}, {NU_TOOL}\n")
    f.write("*DENSITY\n")
    f.write(f"{RHO_TOOL}\n")

    f.write("*SOLID SECTION, ELSET=WORK, MATERIAL=STEEL45\n1.\n")
    f.write("*SOLID SECTION, ELSET=CUTLAYER, MATERIAL=STEEL45\n1.\n")
    f.write("*SOLID SECTION, ELSET=TOOLE, MATERIAL=TOOLMAT\n1.\n")

    # 边界
    f.write("*BOUNDARY\n")
    f.write(f"1, {node_id}, 3, 3\n")  # 全部节点 z 固定（平面应变）
    f.write("*NSET, NSET=WBOT\n")
    f.write(_chunk(", ".join(str(work_nodes[0][j]) for j in range(NX + 1)) + "\n"))
    f.write("*BOUNDARY\n")
    f.write("WBOT, 1, 2\n")
    f.write("*NSET, NSET=WLEFT\n")
    f.write(_chunk(", ".join(str(work_nodes[i][0]) for i in range(1, NY_WORK + 1)) + "\n"))
    f.write("*BOUNDARY\n")
    f.write("WLEFT, 1, 1\n")

    # 刀具顶行: 线性位移边界（恒定速度）
    f.write("*NSET, NSET=TOOLTOP\n")
    f.write(_chunk(", ".join(str(n) for n in tool_top) + "\n"))
    f.write("*AMPLITUDE, NAME=TOOLVEL, TIME=TOTAL TIME\n")
    f.write(f"0.0, 0.0, {CUT_TIME}, 1.0\n")
    f.write("*BOUNDARY, AMPLITUDE=TOOLVEL\n")
    f.write(f"TOOLTOP, 1, 1, {-TOOL_STROKE}\n")
    f.write("TOOLTOP, 2, 2, 0.\n")

    # 接触面
    # TOOLS: 刀具外表面（rake 面=列0 单元 S4；clearance=行0 单元 S1；顶=行7 单元 S3；左端=列7 单元 S4）
    f.write("*SURFACE, NAME=TOOLS, TYPE=ELEMENT\n")
    for r in range(nrow - 1):
        f.write(f"{tool_start + r}, S4\n")  # rake 面（列 0，左边界）
        f.write(f"{tool_start + r + (ncol - 2) * (nrow - 1)}, S2\n")  # 右边界（列 ncol-2）
    for k in range(ncol - 1):
        f.write(f"{tool_start + k * (nrow - 1)}, S1\n")  # clearance 面（行 0）
        f.write(f"{tool_start + k * (nrow - 1) + (nrow - 2)}, S3\n")  # 顶面（行 7）
    # WPS_CHIP: 切屑层外表面（顶 S3 + 右端 S2 + 左端 S4，不含底面 S1=缝面）
    f.write("*SURFACE, NAME=WPS_CHIP, TYPE=ELEMENT\n")
    chip_ids = [e[0] for e in chip_elems]
    nw_chip = CHIP_J1 - CHIP_J0
    for eid in chip_ids:
        f.write(f"{eid}, S3\n")
    for i in range(CUT_ROWS):
        f.write(f"{chip_ids[i * nw_chip + (nw_chip - 1)]}, S2\n")  # 右端列
        f.write(f"{chip_ids[i * nw_chip + 0]}, S4\n")  # 左端列
    # WPS_SEAM: 切屑层底面（缝面）
    f.write("*SURFACE, NAME=WPS_SEAM, TYPE=ELEMENT\n")
    for eid in chip_ids:
        f.write(f"{eid}, S1\n")
    # WORK_TOP: 工件顶面（缝面 master，覆盖整条顶面——切屑层滑动全程可接触）
    f.write("*SURFACE, NAME=WORK_TOP, TYPE=ELEMENT\n")
    work_ids = [e[0] for e in work_elems]
    for i in range(NY_WORK - 1, NY_WORK):
        for j in range(NX):
            f.write(f"{work_ids[i * NX + j]}, S3\n")

    f.write("*CONTACT PAIR, INTERACTION=FRIC, TYPE=NODE TO SURFACE\n")
    f.write("WPS_CHIP, TOOLS\n")
    f.write("*CONTACT PAIR, INTERACTION=FRIC, TYPE=NODE TO SURFACE\n")
    f.write("WPS_SEAM, WORK_TOP\n")
    f.write("*SURFACE INTERACTION, NAME=FRIC\n")
    f.write("*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR\n")
    f.write("2000000.\n")
    f.write("*FRICTION\n")
    f.write(f"{MU_FRICTION}\n")

    # 求解步（显式动态 + 大变形）
    f.write("*STEP, NLGEOM, INC=300000\n")
    f.write("*DYNAMIC, EXPLICIT\n")
    f.write(f"1.E-9, {CUT_TIME}\n")
    f.write("*NODE PRINT, NSET=TOOLTOP, FREQUENCY=1000\n")
    f.write("RF\n")
    f.write("*NODE FILE, NSET=TOOLTOP\n")
    f.write("RF\n")
    f.write("*EL FILE\n")
    f.write("S\n")
    f.write("*END STEP\n")

print(f"生成完成: {OUT}")
print(f"节点: {node_id}, 单元: 工件 {len(work_elems)} + 切屑层 {len(chip_elems)} + 刀具 {len(tool_elems)}")
