# research/ — 科研侧代码库与成果总索引

> **科研端主研究方向（2026-09-19 确立）**：**面向自由曲面的 LLM CAD 代码生成方法**（API 检索接地 + 惯用法技能库 + 分类错误反馈，免训练）。
> 规划与数据：`docs/paper_and_competition/F0`（v2 行动路径）～ `F5`（多条件对比）。
> 实现与实验入口：`engineering/python/app/benchmarks/machinability_pilot/`（曲面基准、五级评估漏斗、grounding 三件套、生成/物理运行器）。

独立科研环境（torch 训练/模型/量化），与 engineering/ 物理解耦。运行：`cd research && pytest tests/`。

## 目录速查（名称即内容）

| 目录 | 内容 |
|---|---|
| [`experiments/`](experiments/README.md) | **实验脚本与成果**（核心库留根 + 5 个主题目录：02_LAM激光抑颤 / 03_PHM2010工具磨损 / 04_物理感知颤振 / 05_论文图件与表格 / 90_临时归档）→ 见其 README |
| [`experiments/results/`](experiments/results/README.md) | 实验产出：训练日志 / 结果 JSON / 论文图件 / 主题结果子目录 |
| [`tests/`](tests/) | 科研侧测试（23 个文件，覆盖标定/闭环/模型/数据/量化等） |
| [`models/`](models/) | 模型定义：base_lnn / hybrid_lnn / ltc / cfc / parameter_models / torch_base_lnn |
| [`training/`](training/) | 训练框架：数据集加载、trainer、evaluator、实验追踪、设备管理、可复现性种子 |
| [`quantization/`](quantization/) | 模型量化（quantizer.py） |
| [`datasets/`](datasets/) | 实验数据集：`piecuch_2025/`、`uniwear/`（uniwear.csv 缺失待下载） |
| [`lab_data/`](lab_data/) | 实验室合成数据（E1/E2 预演 CSV） |
| [`papers/`](papers/) | 论文/报告资料：综合技术文档、大创赛、论文与实验报告 |
| [`phase1_dlnn_v2/`](phase1_dlnn_v2/) | 旧版 DLNN（v2）：训练脚本/配置/日志/PHASE2 方案 |
| [`multimodal_jepa/`](multimodal_jepa/) | JEPA 探索：ijepa_3d / jepa_world_model / vjepa_machining |
| [`prototypes/`](prototypes/) | 原型探索：agents_research / lnn_research / shared |
| [`checkpoints/`](checkpoints/) | 模型检查点（当前为空） |

## 三条实验主线（experiments/ 内）

1. **LAM 激光主动抑颤**（`02_LAM激光抑颤/`，2026-08 论文主线）：热扩展 Tlusty 模型 → 文献标定 → 闭环控制 → r 鲁棒性/多材料/多模态/交叉验证 → 论文初稿与 AI 评审。
2. **PHM2010 工具磨损深度学习**（`03_PHM2010工具磨损/`）：LNN 系列实验（消融、噪声鲁棒、迁移、不确定性等 30+ 项）。
3. **物理感知颤振分析**（`04_物理感知颤振/`）：Tlusty 失配诊断 → 物理感知门控 → 失配增强 → 跨数据集/多场景迁移。

## 关键入口

- 论文初稿：`../docs/LAM_chatter_paper_draft_v1_zh.md`（→ 桌面 `LAM激光主动抑颤论文_初稿v5.docx`）
- 论文评审：`experiments/02_LAM激光抑颤/review_paper_v1.py`（8 采样，结果在 `../docs/review_outputs/`）
- 全量测试：`./.venv/Scripts/pytest tests/ -q`（记得 `env -u PYTHONPATH`）
- **实验脚本运行**（无前缀 import 依赖路径注入，入口固定 `cd research/experiments`）：
  ```bash
  cd research/experiments
  export PYTHONPATH="02_LAM激光抑颤;03_PHM2010工具磨损;04_物理感知颤振;05_论文图件与表格;90_临时归档;..;../..;../../engineering/python"
  ../.venv/Scripts/python.exe 02_LAM激光抑颤/thermal_sld_model.py
  ```
- 环境：`.venv/`（独立 venv，依赖见 requirements.txt）
