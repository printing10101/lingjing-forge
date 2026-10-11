# 完整历史公平对照（2026-10-11）

这是上一批四轮实验的扩展，保留所有旧代码与冻结结果。用户暂时没有真实案例，4段八轮轨迹仍为助手合成，独立审核pending。

两个基线都读取完整历史及自身上一轮提交状态：完整状态重解析、历史辅助增量更新。不是仅给增量模式当前一句造成信息不足。每种32轮，共64次请求上限，无重试、温度0、输出上限768token；错误会沿各自提交状态传播，无标准答案喂入。

临时权限恢复、选择性撤销/重做、历史预算引用、相对高度、引用建议授权、含糊指代与冲突保留均有覆盖。高度和时间作用域约定写入公开上下文。两者都只输出七字段合同，没有任意拓扑编辑、来源验证的新算法。

`artifacts/history_20261011/` 存放审核板、公开输入、独立oracle、32轮人工正确状态的几何参考、64次原始模型记录及评分。几何参考22轮可行并STEP回读、4直接冲突、4授权动作内无候选、2需澄清。不能算作模型端到端成功。

正式64次请求全部完成：完整历史29/32、完整历史辅助增量更新21/32；完整对话分别2/4、0/4。两者澄清标志均32/32正确，所有提案被基础事务规则接受；语义错误仍然存在。增量更新没有胜过强基线，不能引用上一批20/20宣称优势。错误字段包含传播，轮次并非独立样本。

`consequence_diagnostics.json` 对4个错误轮次作定向数值回放，发现错误合同下的可行形状违反标准合同的目标高度/锁定接口/检测截面，另一个错误合同造成假失败。它不是全任务端到端评估，不含预测形状的STEP导出。

评分分别给出原始提案正确、事务接受正确、提交后状态正确。原始非法提案单列，不膨胀为7个字段错误；澄清标志独立计分。`raw_field_errors` 是具体错误字段，`unauthorized_*` 衡量原始提案未获许可的权限增加/锁定解除；模型被拒绝后回滚成功也不能算完成了有效新要求。

```powershell
$env:PYTHONIOENCODING='utf-8'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Set-Location 'D:\灵境制造\research\prototypes\surface_edit_dialogue'
$historyPython='C:\Users\Lenovo\AppData\Local\Programs\Python\Python314\python.exe'
& $historyPython history_experiment.py freeze --output artifacts/new_history
& $historyPython history_experiment.py predict --public artifacts/new_history/public.json --output artifacts/new_history/predictions.jsonl
& $historyPython history_experiment.py evaluate --oracle artifacts/new_history/oracle.json --predictions artifacts/new_history/predictions.jsonl --output artifacts/new_history/scores.json
# 仅中断后续跑：在相同predict命令末尾增加 --resume
```

冻结目录、预测和评分拒绝覆盖。续跑校验公共输入、四份代码、提示和backend设置，并要求结果为有序前缀。API原始记录逐条刷新；中断在途请求可能计费但尚未保存，不能保证计费数量恰好等于已记录条数。

测试24项通过，总覆盖率94.90%，新增两模块均超过80%；测试backend为注入假实现，不调用网络。全部科研原型仍在research侧，没有改变工程后端。

详见 `docs/paper_and_competition/F12-历史引用与临时例外-扩展对照-2026-10-11.md`。本轮是寻找可重复失败的对照实验，不是已经证实有效的新论文方法。
