# 约束曲面编辑先导实验

第一阶段可复现实验，独立于工程运行路径。范围是固定三面拓扑、非修剪、单位权重的正则图形 B-spline；仅沿 z 编辑，采用匹配参数 C1/C2 作为相应 G1/G2 的充分特殊情形。输出是开放三面 compound，**不是封闭实体**。

## 已实现

- OCCT 实际节点插入与升阶；精化前后形状保持检查。
- 最小参考域位移平方积分的传统等式求解器；邻面锁定、接缝、外侧边界、目标及中心位置约束。
- 枚举局部、插节点、升阶、左扩展、右扩展、双侧扩展；按明确的局部性/复杂度优先级选择。
- 独立接缝系数检查、法向/曲率采样、锁定面控制位移界；STEP 写入、回读、B-rep 有效性及 OCCT D2 检查。
- 30 个不同控制网、每个两种权限/优先级，共 60 条待人工审核任务。所有实例来自同一图形曲面族，不代表拓扑泛化。
- 公共输入与 oracle 契约分开保存；语言解析使用已有项目 DeepSeek 后端，不记录密钥。

## 本轮产物

- `artifacts/pilot_20261010_verified/summary.json`：最终传统几何基线，含依赖版本与源码哈希。
- `artifacts/pilot_20261010_verified/results.json`：270 个候选求解记录、选择与回读验收。
- `artifacts/pilot_20261010_verified/geometry/`：30 个原始 STEP。
- `artifacts/pilot_20261010_verified/edited/`：48 个通过检查的编辑 STEP。
- `artifacts/pilot_20261010_verified/oracle_tasks.json`：自动生成、尚未人工审核的契约真值草案。
- `artifacts/pilot_20261010_verified/public_tasks.jsonl`：不含 oracle 的输入。
- `artifacts/pilot_20261010/language_predictions.jsonl`：60 次真实 API 返回，含原始文本、解析结果、耗时。
- 同目录 `language_predictions.config.json`：冻结提示词、请求参数与公共输入哈希；没有密钥。
- `artifacts/pilot_20261010_verified/language_scores.json`：语言模型与模板规则的逐字段评分。

`pilot_20261010` 保留第一轮几何运行和语言原始记录；`pilot_20261010_verified` 是补齐零位移/中心锁定边界校验与来源元数据后重新运行的几何结果。两轮任务清单相同，语言请求没有重跑。

结果：传统基线 48 条找到可行编辑并通过 STEP 验收；12 条在候选集合内无可行解。DeepSeek 需求解析 60/60，模板规则也是 60/60。因此当前实验未证明大模型增益。语言环节只测契约解析，未测模型动作排序或多轮修复，不把传统求解结果称为模型自主几何编辑成绩。

## 复现

使用系统 Python 3.14。当前实测依赖：NumPy 2.5.1、SciPy 1.18.1、cadquery-ocp 7.9.3.1.1；测试需 pytest、pytest-cov；语言客户端复用项目依赖 requests。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Set-Location 'D:\灵境制造\research\prototypes\surface_edit_pilot'
$pilotPython = 'C:\Users\Lenovo\AppData\Local\Programs\Python\Python314\python.exe'
& $pilotPython -m pytest -c pytest.ini --confcutdir=. --cov --cov-config=.coveragerc --cov-report=term-missing --cov-report=json:coverage.json -q
& $pilotPython experiment.py --output artifacts/new_geometry_run --geometry-count 30
```

运行器拒绝覆盖已有任务清单；使用新目录保留冻结产物。几何运行无需网络。

语言请求（会调用项目已配置的 DeepSeek API，每次最多 60 条、每条至多 512 输出 token、无传输重试）：

```powershell
& $pilotPython language_baseline.py predict --public artifacts/new_geometry_run/public_tasks.jsonl --output artifacts/new_geometry_run/predictions.jsonl --limit 60
& $pilotPython language_baseline.py evaluate --oracle artifacts/new_geometry_run/oracle_tasks.json --predictions artifacts/new_geometry_run/predictions.jsonl --output artifacts/new_geometry_run/language_scores.json
```

已保存的本轮结果无需重新调用 API。模型名是 `deepseek-chat` 服务别名，不是不可变版本；旧客户端未返回用量，不能报告精确 token、费用或固定快照。原始预测文件可用于离线复核评分。

## 尚未实现的研究范围

任意修剪/拓扑改变、一般 G2、非单位权重、完整加工与物理约束、模型自适应工具选择、多轮用户意图变化。判定无解仅针对定义好的有限动作集合；数值不相容证据不是任意 CAD 的全局不可行证明。

下一轮采用独立撰写及审定的表达、留出几何族、真实固定尺寸/截面与多轮需求；在任务和验证器冻结前不进行 RL 训练。完整记录见 `docs/paper_and_competition/F9-约束曲面编辑-先导实验第一轮-2026-10-10.md`。
