# research/ — 科研资产与当前状态

更新：2026-10-21。以代码、冻结实验产物和决策记录为准；旧报告中的成功率不等于当前已复现或可投稿。

## 当前工作与保留资产

| 路径 | 状态与用途 |
|---|---|
| `prototypes/pointcloud_registration/` | 最新探索：叶片 STEP 采样、ICP/PCA、对称性与弯度扫描；合成点云探针，尚未确立论文贡献 |
| `prototypes/surface_edit_*` | 方法论文路线已停止投入；保留解析探针、几何内核、强基线、原始模型响应和失败证据，供复核与复用 |
| `prototypes/tool_conditioned_surface_gate/` | 不再按宽泛组合立项；经典球刀几何探针及查新原始记录，保留用于复核 F13/F14 |
| `experiments/` | LAM 抑颤、工具磨损、物理感知颤振与实测验证；历史实验资产，不自动视作当前论文主线 |
| `models/`、`training/`、`quantization/` | 仍有测试和调用的模型、训练及量化基础设施 |
| `datasets/` | Uniwear、Piecuch、力/振动特征、文献数字化稳定性与 FRF 数据；不得混淆实测、数字化、合成和 fixture |
| `multimodal_jepa/` | 工程 DXF 桥接仍使用其中 chamfer 模块，保留 |
| `lab_data/` | E1/E2 合成预演数据，不是实测证据 |
| `papers/` | 历史报告、实验脚本及论文迁出指针；最新私人稿件不以本目录为权威 |
| `phase1_dlnn_v2/` | 已关闭；删除被替代的代码和旧计划，仅保留历史结果与训练曲线 |
| `tests/`、`scripts/` | 科研测试、模型导出工具 |

F0–F6 记录早期 CAD 生成实验；F7–F14 记录复核、转向与停止判定。详见 `../docs/paper_and_competition/`。F6 的 48/50 仅为旧报告口径，旧原始生成批次未在当前仓定位。

2026-10-21 已关闭并移除未接入的 `prototypes/agents_research/`、`prototypes/lnn_research/`。删除依据、清单和验证见 [清理记录](../docs/reports/research_cleanup_2026-10-21.md)。停止的论文表述不等于相关领域没有研究价值。

## 环境与测试

科研侧使用独立 `.venv/`，目前 `pyvenv.cfg` 为 Python 3.12.14；保留该环境，不使用工程侧损坏的旧环境替代。工程侧测试按仓库 AGENTS.md 使用系统 Python 3.14。

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Set-Location 'D:\灵境制造\research'
& '.\.venv\Scripts\python.exe' -m pytest tests/ -q
```

曲面与点云原型按各自 README/测试入口使用系统 Python 3.14。原型测试不属于默认工程 CI 收集范围。不要在清理时删除冻结产物或原始 API 响应。
