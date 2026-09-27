# FEA 交叉验证（CalculiX）— 经验记录

目标：用开源 FEA（CalculiX）做切削仿真，与灵境制造 `cutting_force` 模块（Kienzle/PINN）交叉验证。
验证时间：2026-08-10。

## 环境

- **ccx 2.23 Linux 版**：conda-forge 包解压 + Debian 13 容器（`fea-calculix` 镜像，含 zstd/libarpack 依赖）
- Windows 官方版 `calculix_2.23_4win.zip` 可用（静态/动态求解器），但 Linux 版更稳
- Docker 启动：`docker run --rm --user root -v <fea目录>:/fea fea-calculix bash -c "cd /fea && ./ccx_lnx/bin/ccx <job>"`

## 已跑通

### 1. 悬臂梁验证（工具链校准）
- `cantilever.inp`：B31 梁单元，L=100mm, b×h=10×10, E=210GPa, 端部 1000N
- 结果：FEA 1.8835mm vs 理论 1.9048mm，**误差 1.1%**（4 段梁离散误差，正常）

### 2. 等效切削力验证（Kienzle vs FEA 应力场）
- `equivalent_load.inp`：工件 2.5×1.0mm 平面应变（CPE4R），底边固定
- 载荷：Kienzle 预测力 Fz=355.7N（-x）+ Fx=106.7N（-y）施加在刀尖节点
- 结果：最大 von Mises **654 MPa @载荷点旁**，载荷点位移 0.005mm
- **结论**：654MPa > 45 钢 JC 屈服 553MPa → Kienzle 力量级与"切削区必须塑性屈服"的物理事实一致，**灵境 Kienzle 模块预测物理自洽** 

## 踩坑记录（重要）

1. **CalculiX 2.23 *DAMAGE INITIATION 与接触不兼容（通用 bug）**
   - `*DAMAGE INITIATION` 与任何 `*SURFACE INTERACTION` 共存即报错：
     `a damage initiation model was defined for material<交互名> for which no equivalent strain is calculated`
   - Windows 官方版与 Linux 官方版都复现 → 非移植问题
   - 绕过：预置分离缝（切屑层与工件双节点网格 + 接触对连接），不用单元删除
2. **显式语法**：`*DYNAMIC, EXPLICIT`（参数在关键词上），数据行 = 初始增量, 总时长
3. **MASSLESS 接触**：显式专用，但 slave 节点不允许任何 SPC（平面应变 U3=0 冲突）→ 用 NODE TO SURFACE
4. **JC 本构语法**（2.23）：`*PLASTIC, HARDENING=JOHNSON COOK`（A,B,n,m,Tm,T0）+ `*RATE DEPENDENT, TYPE=JOHNSON COOK`（C, ε̇₀）
5. **frd 解析**：数据行是定宽无分隔格式（`17-4.96580E-03`），行首有前导空格，用正则 token 提取（见 analyze_frd.py）
6. **正交切削几何**：刀具向左移动时，切屑层在 rake 面左侧（未切削区在运动前方）；rake 面必须与切屑层右端共面（浮点用 round 对齐网格）
7. **NSET/ELSET 数据行 ≤16 项/行**（CalculiX 行限制）
8. **隐式动态+接触+大变形不收敛**（切削场景）；显式能跑但力振荡

## 未完成（正交切削完整切屑形成）

- 预分离缝模型显式求解：力在 ±50~120N 振荡、稳态均值≈0（切屑层自由体弹跳，非塑性剪切主导）
- 尝试过：摩擦 0.4→0.9、速度减半、接触刚度 7e6→2e6 → 无效
- **结论**：CalculiX 做完整切屑形成仿真受限（无单元删除 + 接触 bug）。论文级切屑形成建议：
  - Abaqus（学校授权，*SHEAR FAILURE 单元删除）
  - AdvantEdge（切削专用）
  - 或 CalculiX 预分离缝 + 隐式准静态继续调（低优先级）

## 文件清单

| 文件 | 用途 |
|---|---|
| `cantilever.inp` | 悬臂梁验证 |
| `equivalent_load.inp` | 等效切削力验证 |
| `generate_orthogonal.py` | 正交切削模型生成器（预分离缝版） |
| `orthogonal_cut.inp` | 生成的正交切削模型 |
| `analyze_cut.py` | 反力-时间曲线 + Kienzle 对比 |
| `analyze_frd.py` | 通用 .frd 解析器 |
| `ccx_win/` | Windows 版 CalculiX 2.23 |
| `ccx_lnx/` | Linux 版（容器内使用） |
| `ccx_htm/` | CalculiX 2.23 HTML 手册（本地离线查） |
