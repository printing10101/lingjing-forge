# 薄壁最终零件反向设计：探索原型与阶段台账

最初试算日期：2026-11-10；阶段收束：2026-11-11。用户已确认优先研究**最终零件的壁厚或加强筋**；首轮研究三段壁厚，后续扩展至薄板与来源诊断，不研究牺牲筋、余量或生产系统接入。

当前总入口：[F17阶段台账](../../../docs/paper_and_competition/F17-薄壁最终结构逆设计探索台账-2026-11-11.md)。机器记录与文件指纹在`ledger/20261111/`。按用户要求先收束，不新增试算；四轮试算记录、第五轮锚点来源核验（2026-11-11，判定候选不满足完整可复核条件）、25点暂定标签、已撤回27点版本和1个数值未决项分开保留。当前尚未证明新设计规则稳定优于强基线，未完成薄壁外部实验验证或加强筋实现。

本目录是可运行的探索性数值核查，**不是已完成的论文方法，不是实切验证，也不构成分区承诺**。题目中“静刚度最优未必抗颤振最优”是已有动力学知识，本轮仅用它校验计算链，不能包装成创新。

## 第一轮完成

- Euler-Bernoulli 梁条有限元：一致质量矩阵、根部固支、接触位置 Hermite 插值、质量归一化多模态。
- 再生切削时滞方程：平均方向系数频域筛选、周期性方向系数半离散 Floquet 复核、单模态 RK4 时域交叉检查。
- 61 个同质量三段壁厚方案，3 个接触位置、19 个转速，共 3477 个确定性场景评估。
- 均匀壁厚、网格内静柔度最小、网格内最差边界最大、网格内平均边界最大四种选择规则。
- 网格、模态数、频率扫描、时间离散和阻尼/接触位置敏感性核查；冻结配置、源代码与版本哈希。
- 本地工程/科研相关资产盘点，7 个 DOI 元数据和 6 组 OpenAlex 检索原始响应。

## 第一轮结果

运行 `artifacts/run_20261110_01/`：静柔度最小和最差稳定性边界最大的方案**相同**，均为根部至顶部 `[2.75, 2.0, 1.25] mm`。平均边界最大的方案为 `[2.5, 2.25, 1.25] mm`，在周期模型中的三个冻结转速检查中有利有弊。

结论：计算链已建立；尚未证明一个新设计准则优于静刚度强基线。完整解释见仓库 `docs/reports/thinwall_final_design_feasibility_20261110.md`。

## 第一轮模型与边界（梁条）

SI 单位。梁条高 80 mm、宽 20 mm，平均厚度 2 mm，E=70 GPa，密度 2700 kg/m3；质量均为 8.64 g。几何、材料、切削系数与阻尼是**声明的示例假设**，不是用户真实零件或文献标定值。

方程为：

`q'' + 2*zeta*Omega*q' + Omega^2*q = -b*Kt*h(t)*v*v.T*(q(t)-q(t-tau))`

其中 `v` 为质量归一化模态在刀具接触位置的参与因子，`tau=60/(rpm*teeth)`；`h(t)` 为无螺旋角、半浸入及以下逆铣的单方向投影系数。实现与方向定义见 `mechanics.py`。模型包含再生反馈，不是仅施加周期外力。

尚未包含：板壳效应、加强筋、弹性夹持、刀具柔性、螺旋刃多点接触、材料去除全过程、过程阻尼、刀具脱离/非线性切屑厚度、最终服役载荷与应力约束、加工可达性。**同质量不等于已满足全部同功能约束**。点接触和模态截断下的绝对切深，尤其不能直接作为上机参数。

`periodic_boundary` 采用几何递增括区和求根，返回采样到的首个失稳括区交点；不保证穷尽很窄的再入稳定区。频域扫描也仅覆盖声明的截断模态与频率范围。网格和步长变化小属于数值证据，不是现实正确性的证明。

## 重跑

使用现有科研环境；本轮未安装新依赖、未修改全局环境。已实测 Python 3.12.14、NumPy 2.5.3、SciPy 1.17.1、Matplotlib 3.10.0。不要为了重跑原型安装整个旧训练依赖表并降级现有环境。

在仓库根目录 PowerShell：

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
$thinwallPython='D:\灵境制造\research\.venv\Scripts\python.exe'
& $thinwallPython research/prototypes/thinwall_inverse_design/pilot.py screen --output research/prototypes/thinwall_inverse_design/artifacts/run_new
& $thinwallPython research/prototypes/thinwall_inverse_design/pilot.py verify --output research/prototypes/thinwall_inverse_design/artifacts/run_new
& $thinwallPython research/prototypes/thinwall_inverse_design/refine.py --output research/prototypes/thinwall_inverse_design/artifacts/run_new
```

输出目录必须全新；已有运行不覆盖。复核阶段会拒绝源代码相对于筛选快照发生漂移的运行。`refine.py` 是原运行之后的自适应数值检查，其自身不在初始筛选快照内，独立留存说明，不伪装成预先规定的确认性实验。

科研侧测试：

```powershell
Set-Location 'D:\灵境制造\research'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& '.\.venv\Scripts\python.exe' -m pytest prototypes/thinwall_inverse_design/tests/ -q
```

系统 Python 3.14 已补充检查与覆盖率测量，配置 `coverage.ini` 仅作用于本原型，报告在 `evidence/test_coverage.json`，不更改根覆盖率门禁或工程 CI 收集规则。

## 原始结果

| 文件 | 用途 |
|---|---|
| `pilot_config.json` | 首次运行前确定的探索问题、对照、参数、项目门槛和预算 |
| `artifacts/run_20261110_01/source/`、`manifest.json` | 冻结代码、配置、环境、SHA-256 |
| `screening.csv`、`screening_summary.json` | 全部场景、设计分数和选择结果 |
| `screening.png` | 平均模型筛选图，不能冒充实切效果图 |
| `verification.json` | 收敛、阻尼/位置核查、周期模型和单模态时域检查 |
| `step_refinement.json` | 未通过 80/160 步门槛工况的 320 步追加核查 |
| `evidence/inventory_and_metadata.json` | 分侧资产计数、CSV 来源字段、DOI 元数据与 API 检索响应 |

所有场景是确定性模型评估，不按 3477 个独立实测样本计算显著性。没有训练/验证数据集，也没有用同源仿真冒充外部实验验证。

## 第二轮：薄板与外部实验核对

完整记录：`docs/reports/thinwall_plate_and_external_validation_20261110.md`。

- `plate.py` / `plate_pilot.py`：全局多项式薄板，保留了低阶模型假可行的失败记录。
- `plate_spline.py` / `spline_pilot.py`：界面相容C1分片薄板；85候选在4/6/8分段重算，最细43种满足三项示例服务刚度约束。真实产品的应力/屈曲/加工可达性仍未包含。
- `coupled_milling.py` / `benchmark.py`：双方向再生切削模型与Gradišek2005现有数字化图标签核对，不拟合标签。320步结果353/516=68.41%；第三轮确认旧表标签未可靠核验，该数字仅为与旧表一致率，不能作为实验准确率或薄壁设计有效性验证。
- 第二轮完整科研测试51项通过；新增六模块覆盖率97.58%。这不是物理可信度或全仓覆盖率。

在 `research/` 内运行（保持PYTHONPATH清空，使用现有科研环境）：

```powershell
& '.\.venv\Scripts\python.exe' -m prototypes.thinwall_inverse_design.spline_pilot screen --output prototypes/thinwall_inverse_design/artifacts/spline_new
& '.\.venv\Scripts\python.exe' -m prototypes.thinwall_inverse_design.spline_pilot verify --output prototypes/thinwall_inverse_design/artifacts/spline_new
& '.\.venv\Scripts\python.exe' -m prototypes.thinwall_inverse_design.benchmark --output prototypes/thinwall_inverse_design/artifacts/benchmark_new
```

第二轮基准320步追加检查脚本位于 `artifacts/gradisek_20261110_02/refinement_320.py`，应从 `research/` 以 `-m prototypes.thinwall_inverse_design.artifacts.gradisek_20261110_02.refinement_320` 运行；原结果已冻结，复查时使用独立副本目录，勿覆盖来源记录。


## 第三轮：来源核查与第二版标注

完整记录：`docs/reports/thinwall_reference_audit_20261111.md`。固定抽查32行，第二版暂可辨25行，其中1行旧标签明确有误，7行节点/重叠不进入分类参照。助手第一版视觉标注误判了3处，27点旧导出与相关声明已撤回并留存。

`reference_audit.py`保留原CSV，通过来源哈希、审阅版本与理由导出第二版25点。当前默认`benchmark`锁定`reviewed_reference_20261111_02`；固定参数模型在80/160步均符合21/25，不能外推总体准确率。原图低分辨率，单助手审阅与旧坐标仍有局限。来源检查测试不能证明标注或物理正确。

螺旋角0°/45°对照在320步时25点逐点判断相同，4个偏差全部保留；仅该近似不能解释偏差。设置与全量结果位于`artifacts/helix_diagnostic_20261111_01/`。第三轮原型60项检查通过，来源导出/基准入口补充覆盖率98.26%。


## 第四轮：频率观测与未识别柔度的独立诊断

`speed_dynamics.py`/`speed_diagnostic.py`从Fig8的33点频率表插值，分别声明固定等效质量、固定静态柔度和α=0.75示例。六个设置使用全部25点，不依标签拟合。静止基线21/25；前两种频率修正均18/25，修正1个旧偏差同时新增4个。α=0.75当前20/25，其中1个点在1280步仍未满足保守收敛筛查，不声明已充分收敛。频率相同、柔度闭合不同可改变判断，未知参数不能按分数择优。

完整报告：`docs/reports/thinwall_speed_dynamics_diagnostic_20261111.md`。原型76项检查通过，本轮两个模块覆盖率100%；这不是物理或总体实验正确性的证明。新增薄壁候选锚点仅登记、未验收，详见`evidence/round4/primary_sources_and_next_anchor.json`。

从`research/`运行：`& '.\.venv\Scripts\python.exe' -m prototypes.thinwall_inverse_design.speed_diagnostic --output prototypes/thinwall_inverse_design/artifacts/speed_new`。保持PYTHONPATH清空并使用现有科研环境；默认旧基准未被本轮假设替换。
