# 约束曲面编辑解析诊断

选题验证用，非工业 CAD 编辑器，非 LLM 评测。只支持三片匹配参数的正则 Bézier 图形曲面、固定 xy、单位权重、z 位移与 C0/C1/C2 等式约束。

`probe.py` 运行 243 次确定性参数扫描，另测一个真正矛盾的中心锁定约束；`test_probe.py` 包含独立多项式构造真值与故意破坏接缝后的检出测试。`results.json` 是原始结果。完整边界、方法方案与推进条件见仓库 `docs/paper_and_competition/F8-约束曲面编辑-价值验证与方法设计-2026-10-10.md`。

PowerShell 复现：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Set-Location 'D:\灵境制造\research\prototypes\surface_edit_probe'
$probePython = '<用户主目录>\AppData\Local\Programs\Python\Python314\python.exe'
& $probePython probe.py --output results.json
& $probePython -m pytest -c pytest.ini --confcutdir=. --cov=probe --cov-config=.coveragerc --cov-report=term-missing --cov-report=json:coverage.json -q
```

需要 NumPy、pytest、pytest-cov。测试与工程侧收集配置隔离。没有调用外部模型、CAD 内核或进行训练；提高次数策略仅在新表示中解位移，不含生产级节点插入、升阶和 STEP 回读实现。
