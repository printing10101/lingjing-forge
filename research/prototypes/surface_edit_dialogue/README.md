# 多轮设计意图与约束维护先导实验

最新扩展见 [HISTORY_EXPERIMENT.md](HISTORY_EXPERIMENT.md)：2026-10-11的32轮完整历史公平对照，完整状态重解析29/32、增量更新21/32。下面20轮旧结果只对应先导设置，不能据此宣称增量方法更优。

5 个几何（条带、二维拼接、带孔区域），每个 4 轮；复用上一轮 `surface_edit_challenge` 的原始几何和求解器。数据由实施助手撰写，**独立人工审核 pending**，不是用户真实操作数据，也不属于已经验证的新方法。

规则：所有高度相对初始交付模型；替换高度不累加；未明确撤销的要求持续有效；几何冲突不自动删除约束；含糊请求先澄清。本轮每次从原始控制网按累计意图重新求解，并非在上一轮修改后的曲面上继续编辑。外边界、孔口、光滑 C2 与锐边规则由求解器固定执行，不允许通过状态字段撤销。

`dialogue.py` 包含原子状态更新、20 轮草案、人工正确状态的几何参考路径。一次状态事务全部校验通过才提交；澄清请求不改状态。当前状态仅七字段，没有稳定拓扑引用迁移、任意约束图、撤销重做或自然语言权限证明。

`compare.py` 使用项目已有 DeepSeek backend；完整历史重解析对照结构化增量更新。前者每轮读取全部用户历史；后者读取上次自身预测的状态和本轮请求。两者使用同一模型、系统提示、公开几何和上下文，不给标准答案或几何求解反馈。错误会在增量更新路径传播；不做 teacher forcing。增量 JSON + 确定性校验目前只是基线，不是论文贡献。

每批请求上限 40，每次至多 768 输出 token，温度 0，无传输重试。原始响应、解析失败和实际请求时长逐条保存；模型别名不是不可变快照，现有客户端没有 token 使用量。输入、源代码、backend 哈希写入配置，不复制或记录密钥。

产物 `artifacts/dialogue_20261010/`：

- `review.md`：逐轮文本、更新草案、几何判定和待审核栏。
- `public.json`：无答案的模型输入；`oracle.json`：单独保存标准状态草案。
- `geometry_results.json`：人工正确状态下的求解结果；14 个可行轮次有 STEP 回读。
- `formal_predictions.jsonl`、`formal_predictions.config.json`：正式模型原始记录和请求配置。
- `formal_scores.json`：状态准确率及保留/更新字段错误。没有把人工正确状态的几何成功计成模型端到端成功。
- `predictions.jsonl`、`calibration_scores.json`、`calibration_compare.py`：首次40请求的校准记录与代码快照。首次提示缺少优先级枚举语义，随后明确说明并另跑正式40次；不拼接两批优选结果。总计80条已保存响应。
- `completion_audit.json`：2026-10-11完成审计、续跑记录、原始失败细分。已保存80条响应，中断时另一次在途请求是否计费未知，不能保证实际计费不超过80次。

正式结果：完整历史19/20，增量更新20/20。唯一差异是含糊授权时，完整历史原始响应提出澄清却同时恢复加密权限/预算；事务被拒绝，旧状态未变。整轮失败计分中的7个字段错误不能读成7个语义误删；原始提案实际错改2字段。小样差异不足以证明学术贡献。

几何仍限定单位权重、仿射 xy 参数、z 向位移、固定拓扑、未修剪图形 B-spline。匹配物理 C2 是 G2 的特殊充分条件；输出是开放面 compound，不是缝合实体，没有加工验证。

```powershell
$env:PYTHONIOENCODING='utf-8'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Set-Location 'D:\灵境制造\research\prototypes\surface_edit_dialogue'
$dialoguePython='C:\Users\Lenovo\AppData\Local\Programs\Python\Python314\python.exe'
& $dialoguePython -m pytest -c pytest.ini --confcutdir=. --cov --cov-config=.coveragerc --cov-report=term-missing -q
& $dialoguePython dialogue.py --output artifacts/new_run
& $dialoguePython compare.py predict --public artifacts/new_run/public.json --output artifacts/new_run/predictions.jsonl
& $dialoguePython compare.py evaluate --oracle artifacts/new_run/oracle.json --predictions artifacts/new_run/predictions.jsonl --output artifacts/new_run/scores.json
```

冻结产物拒绝覆盖。测试使用注入的假 backend，不调用网络；实际预测命令调用 API。16 项测试通过，三个新模块覆盖率均超过 80%，总覆盖率 95.47%。

中断时可执行 `resume.py --public artifacts/new_run/public.json --predictions artifacts/new_run/predictions.jsonl`，只补齐经校验的缺失前缀之后的轮次，输入/代码/提示不一致时拒绝续跑。原始记录均保留，可能未保存的在途请求成本需单列。

下一阶段须先审核文本语义，再引入真实工程修改记录、更多无关轮次及跨对象指代。应同时报告完整对话成功率、硬约束误撤销、目标替换错误、澄清准确率和人工修正次数；状态准确率不能替代全部指标。本轮没有测人工修正次数，也没有训练或 RL。
