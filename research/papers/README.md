# papers/ — 论文与报告资料

| 内容 | 说明 |
|---|---|
| `01-综合技术文档.md` / `04-机械方向内容报告.md` | 综合技术文档 |
| `版本号验证报告_V2.5.0.md` | 版本一致性验证报告 |
| `thesis_content.json` | 论文内容数据 |
| `integrated/` | 整合文档（`_src/` 为各论文 md 源） |
| `report_assets/` | 报告素材 |
| `大创赛/` | 大创赛材料 |
| `论文相关/`（脚本/、论文与实验报告/） | 论文写作相关脚本与报告 |

## 论文清单

正式论文（docx 在 `论文相关/论文与实验报告/`，md 源在 `integrated/_src/`）：

| 论文 | 主题 | 目标期刊 | 状态 |
|---|---|---|---|
| 论文1_DL-LNN颤振预测主论文 | 基于连续时间液态时间常数网络的铣削颤振稳定性预测 | JIM (Q1) | 初稿 v1.0，待补 benchmark/LOMO·LOCO/消融 |
| 论文2_PCC_Loss通用化方法论 | 梯度层物理约束 PCC Loss 通用方法论 | CMAME / EAAI | 初稿 v0.1，待补跨领域验证 |
| 论文3_双分支门控融合架构 | 数据-物理双分支门控融合（小样本自适应置信度加权） | MSSP / JMS | 初稿 v0.1，待补跨工况验证 |
| 论文4_连续时间神经网络制造应用综述 | CTNN 在智能制造中的应用系统性综述 | JMS / IISE Trans | 初稿 v0.1，待补文献计量 |
| 论文5_LAM激光主动抑颤（`LAM论文.docx` 当前版 + `LAM激光主动抑颤论文_初稿v5.docx` 早期初稿） | 激光功率调制作为颤振主动抑制热执行器（热扩展 SLD 解析 + 文献标定 + 时域闭环） | JMP (Q1) → MSSP 备选 | 中文初稿 v2，全仿真证据链 + OA 实测交叉验证；审稿前需按 §12 实验计划补物理验证 |

配套资产：LAM 仿真与实验脚本 `research/experiments/02_LAM激光抑颤/`（thermal_sld_model.py、exp_thermal_sld.py）、测试 `research/tests/test_thermal_sld_model.py`、草稿历史 `docs/LAM_chatter_paper_draft_v1_zh.md`（v1）。