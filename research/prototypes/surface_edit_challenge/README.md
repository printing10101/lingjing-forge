# 约束曲面编辑：10 个几何 / 30 条需求

本轮交付是可复现的**助手撰写、待独立人工审核**挑战集。没有进行新的模型请求或训练。工程侧代码和上一批实验文件未修改。

## 资产与结果

`artifacts/challenge_20261010/`：

- `review_board.md`：30 条完整需求、逐案结构示意、可行候选及审核栏。
- `decision_table.csv`：逐任务可行方案、选择与失败原因；审核状态为 pending。
- `public_tasks.jsonl`：文本、STEP 路径及公开几何角色，不包含 oracle 或答案。
- `oracle_tasks.json`：语义契约草案及标注依据，独立保存。
- `geometry/`：10 个原始 STEP、控制网 JSON 和 xy 邻接示意 SVG。
- `edited/`：23 个通过定义好的验收的编辑 STEP。
- `results.json`：194 次候选评估及逐案回读验收。
- `summary.json`：来源哈希、依赖版本、基线统计。
- `c07_B_diagnosis.json`：放松检测截面/加密权限的诊断对照，明确不是原任务的合法解。
- `baseline_replay.json`：固定顺序和按成本分组的传统策略回放；调用计数不冒充实测新时延。

几何包括 4 个条带、3 个二维网格、3 个带中心孔的环形邻接结构。5 个实例含真实斜率跳变锐边。曲面均为单位权重、仿射 xy 参数、z 向位移的图形 B-spline，不支持一般修剪/重参数化；C2 是 G2 的充分特殊情形。输出是独立开放面的 compound，未缝合为壳或封闭实体。

30 条任务：23 条找到可行解并通过 STEP 回读；6 条存在“固定截面穿过抬高点”的直接冲突；1 条在原控制网下不相容，但放开加密后可行。最后这一条不能称为与表示无关的无解。

弱固定加密/扩展策略在 23 条可行任务上分别完成 9/23、11/23。更强的传统策略按权限/优先成本分组、接收求解反馈后停止，回放与全枚举 30/30 一致，预计调用从 194 降至 147。因此本轮没有证明需要 LLM。

## 复现

实测 Python 3.14.4、NumPy 2.5.1、SciPy 1.18.1、cadquery-ocp 7.9.3.1.1；测试需要 pytest、pytest-cov。复用邻目录 `surface_edit_pilot/geometry.py` 和 `kernel.py` 的表示与内核适配，哈希记录在结果中。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Set-Location 'D:\灵境制造\research\prototypes\surface_edit_challenge'
$challengePython = 'C:\Users\Lenovo\AppData\Local\Programs\Python\Python314\python.exe'
& $challengePython -m pytest -c pytest.ini --confcutdir=. --cov --cov-config=.coveragerc --cov-report=term-missing --cov-report=json:coverage.json -q
& $challengePython runner.py --output artifacts/new_challenge_run
& $challengePython -c "from diagnostics import save; save('artifacts/new_challenge_run/c07_B_diagnosis.json')"
& $challengePython -c "from baselines import save; save('artifacts/new_challenge_run')"
```

运行器和补充分析拒绝覆盖冻结产物。全部步骤只在本地执行，无 API 费用。

本轮实测 27 项测试通过，行覆盖率 95.29%，所有新增模块均超过 80%。

## 审核事项

对每条任务检查：自然语言是否唯一对应契约；目标面、接口面和截面是否正确；“先不增加控制点”是否被理解为软优先级而非硬禁令；公共 C2 与锐边约定是否可接受；控制点预算是否合理；无解分类是否仅限允许的表示/动作；STEP 是否符合角色标注。

目前已有程序检查、独立多项式构造解和反事实诊断，但没有独立专家/用户审核，不得把审核栏自动勾选。几何验证不证明自然语言标注无歧义。

后续方法至少应与 `priority_group_replay` 对应的确定性分组求解策略比较，统一候选及预算。分组回放依赖正确契约，尚未评测自然语言理解；也未证明该传统方法在大规模动作空间中最省调用。

完整判定见 `docs/paper_and_competition/F10-约束曲面编辑-十案例三十任务与强基线-2026-10-10.md`。
