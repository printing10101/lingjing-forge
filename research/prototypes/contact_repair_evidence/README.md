# 刀具接触与受保护几何的证据驱动修正原型

2026-11-08 新建，**尚未确立专利新颖性或创造性**。独立科研原型，不接入工程运行链。

## 本轮要区分的解释

1. 粗曲面表示下未找到解，不等于设计要求与刀具必然冲突。对照固定五次表示、均匀插节点、接触对偶权重引导的单区间插节点。只有精化后找到并验证同一要求的解，才证明该实例原先受到表示限制。
2. 离散采样通过，不等于连续截面无超容差侵入。用独立的有理数 Bernstein 系数与二分验证；预算不足必须返回 unknown。
3. 精确锁定的点已进入刀球，是该固定姿态下与锁定条件的直接冲突；不能通过改变该锁定点修复。

另以 `z0(x)-B` 作为全部允许竖向修改的点态下界。若该下界仍有经有理数证实的侵入点，则任意此类图形编辑都不能在全域位移预算 B 内修复；这与当前样条表示无关。接触必要改变量区间与可行候选实际位移的上下界由 `analyze_bounds.py` 计算；该分析是看过首轮结果后的探索性分析，不能包装成预注册结果。

主终点是上述分类正确性及连续检查；次终点为相同控制点预算下的求解次数与成功率。几何均为合成截面，同一形状的尺度复制不是独立零件；不做统计显著性或泛化声明。所有策略共享样本、锁定条件、预算、容差和验证器。若靶向精化不优于均匀精化，如实记录，不能靠换案例宣称优越。

## 数学范围

固定球心 (0,r)，材料在图形截面 z(x) 以下，域完全位于 [-r,r]。验证 z <= r-sqrt(r²-x²)+eps。它等价于 U=r+eps-z>=0 且 U²-r²+x²>=0。以精确有理数进行节点插入、Bernstein 乘积和细分，对传入二进制浮点数所精确定义的曲线作证明。eps 是允许侵入容差，**不是安全余量**。

尚无刀柄、运动刀轨、完整三维可达性、热/切削变形或 ANSYS。截面失败可作为相应截面侵入证据；截面通过不能认证任意三维模型。优化与设计锁定使用浮点数并独立检查残差；只有接触不等式部分采用有理数证明。对偶权重仅是精化启发式，不是新的 Farkas 定理。

## 运行

在本目录清除 PYTHONPATH，使用系统 Python 3.14：

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& 'C:/Users/Lenovo/AppData/Local/Programs/Python/Python314/python.exe' -m pytest -c pytest.ini --confcutdir=. --cov --cov-config=.coveragerc --cov-report=term-missing -q
& 'C:/Users/Lenovo/AppData/Local/Programs/Python/Python314/python.exe' experiment.py --output artifacts/new_run
& 'C:/Users/Lenovo/AppData/Local/Programs/Python/Python314/python.exe' analyze_bounds.py --run artifacts/new_run --output artifacts/new_bounds.json
```

复用项目既有 NumPy/SciPy 环境、B-spline 与 STEP 内核适配。新旧冻结产物不覆盖。完整加工真值与神经网络收益均未建立，不把验证器或启发式组合声称为已可授权新算法。

## 2026-11-08 结果（历史批次）

最终冻结批次 `artifacts/run_20261108_03/`：20 组配置 × 3 策略，共60次；固定表示8组通过，均匀精化16组通过，靶向精化16组通过。每个策略都识别2组位移预算冲突、2组受保护点冲突。固定表示另8组为表示内未解决，不判成全局无解。初版 `_01`、加入预算判据的 `_02` 均保留，最终代码指纹以 `_03` 为准。

`artifacts/bounds_20261108_02.json`：r=8 mm、预算0.6 mm、C2端部锁定的代表配置，接触必要改变量约0.10798023–0.10798030 mm；均匀精化候选实际最大改变量约0.11044379–0.11044385 mm，靶向候选约0.22556617–0.22556624 mm。靶向策略在该例使用更少控制点，但几何改动更大，不能宣称全面领先。

37项测试通过，新增模块总行覆盖率96.28%，每模块均超过80%；5个故意绕过验证/锁定/预算/回读的变异均被对应测试捕获，结果在 `artifacts/mutation_20261108.json`。这些是当前原型内的验证，不是工业安全等级或专利授权证据。

## 2026-11-09：实际几何 minimax 与协同诊断

新增 `envelope_solver.py`、`envelope_policy.py`、`envelope_experiment.py`、`coupling_probe.py`，不改变已冻结的11月8日算法源码。新求解器以真实竖向最大位移为主目标：精确锁定零空间、向外球面包络的放松LP、对偶证据有理数重建、连续接触与位移界、逐区间反例反馈。固定网格L1只用来在接近minimax最优值的范围内择解，不代替连续主目标。

`artifacts/envelope_20261109_01/` 冻结14组配置×7策略，共98次。新均匀、对偶局部、直接13/21控制点都在9组可行要求上达到统一复算的质量门槛；逐区间试探仅5组达到，另4组只满足编辑预算。每策略另有4组预算冲突和1组保护点冲突。直接13控制点用了165次LP，新均匀312次、对偶局部525次、逐区间试探783次；新选择策略尚未超过强基线。

新方法接受的45个结果均回放精确锁定、连续可行性和对偶证据。代表三面STEP已回读，新增实际位移预算复验；导出前锁定为精确有理等式，回读后的设计审计仍为内核数值检查，不能混为同一保证。

`artifacts/coupling_probe_20261109.json` 是观察主结果后的11次诊断：在一个固定9控制点例子中，单节点改进最多2.2204e-6 mm，而成对插入(-1,1) mm改进至少0.00461956 mm。说明即时贪心会漏掉成组收益，但普通对称成对精化也能做到，不据此认定专利新颖性。报告目录另保存6组配对交互量区间，只有一个源截面，未训练网络。

当前54项测试通过，总行覆盖率96.73%，各模块超过80%；5个新增故意错误均被回归测试捕获。证据在 `artifacts/coverage_20261109_final.json` 与 `artifacts/envelope_mutation_20261109.json`。

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& 'C:/Users/Lenovo/AppData/Local/Programs/Python/Python314/python.exe' -m pytest -c pytest.ini --confcutdir=. --cov --cov-config=.coveragerc --cov-report=term-missing -q
& 'C:/Users/Lenovo/AppData/Local/Programs/Python/Python314/python.exe' envelope_experiment.py --output artifacts/your_new_run
& 'C:/Users/Lenovo/AppData/Local/Programs/Python/Python314/python.exe' coupling_probe.py --output artifacts/your_new_probe.json
```

最新技术报告：`docs/paper_and_competition/F16-可核验最小改动修正与协同精化候选-2026-11-09.md`（从项目根目录定位）；PDF为 `output/pdf/可核验CAD最小改动修正与新算法候选报告.pdf`。本轮仍无新ANSYS数据、已训练神经网络或完整加工验证，尚未确立可授权新算法。
