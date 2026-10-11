# 刀具条件化曲面：前置物理探针

这是原“多轮约束账本”论文路线在G0未通过、停止实现和模型调用后，对替代方向进行的零API筛查。**不是新算法、不是已成立论文骨架。**

同一抛物柱面，材料位于其下方，刀球在谷底原点相切。改变球半径，解析比较刀球下缘与曲面的位置；负间隙证明该固定姿态的局部过切。所有表面法向仍向上。公式和原理是经典几何，不能据此宣称论文创新。

9组为3种尺寸缩放×3种刀球半径，只有一个几何结构。没有刀柄、进退刀、完整刀轨、STEP或实物加工；零间隙表示预期接触，不表示保守安全余量。

`probe.py` 输出解析极值、数值采样极值、法向性质及来源哈希；测试的密集采样使用独立直接平方根表达式。11项测试通过，覆盖率93.18%。测试和探针均无网络请求。

```powershell
$env:PYTHONIOENCODING='utf-8'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Set-Location 'D:\灵境制造\research\prototypes\tool_conditioned_surface_gate'
$gatePython='<用户主目录>\AppData\Local\Programs\Python\Python314\python.exe'
& $gatePython -m pytest -c pytest.ini --confcutdir=. --cov --cov-config=.coveragerc --cov-report=term-missing -q
& $gatePython probe.py --output artifacts/new_gate.json
```

拒绝覆盖已有结果。冻结结果：`artifacts/gate_20261011.json`。

后续查新已完成本轮筛查：宽泛的“LLM＋锁定区域＋连续性＋指定刀具约束”组合未通过G0，停止扩展模型实验。不是证明整个领域没有研究空间。判决详见 `docs/paper_and_competition/F14-刀具条件曲面路线查新与停止判定-2026-10-11.md`；前置记录为F13。

`artifacts/occupancy_20261011.json`保存8条OpenAlex查询、实际响应总数及每式最多8条原始条目；含2条已知论文正对照。检索数量不是新颖性证据，原始结果不得覆盖。
