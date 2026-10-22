# experiments/ — 历史实验与可复用研究基础

更新：2026-10-21。本目录不再统一称作“当前论文主线”。

| 路径 | 内容 |
|---|---|
| 根目录核心库 | `models.py`、`config.py`、`metrics.py`、`losses.py`、`data_generator.py`、`trainer.py`、`train_models.py`；仍被测试与实验引用 |
| `02_LAM激光抑颤/` | 热稳定性、标定、闭环、鲁棒性与实验协议；保留用于复现 |
| `03_PHM2010工具磨损/` | 模型对比、消融、噪声、迁移、不确定性等历史实验 |
| `04_物理感知颤振/` | 模型失配、门控与跨数据集诊断 |
| `05_论文图件与表格/` | 历史图件与报告生成工具 |
| `05_SSM预测backbone/` | SSM 训练/导出脚本；相关模型与测试仍存在 |
| `real_validation/` | 实测数据接入、数字化与独立验证；逐源检查 fixture 和适用边界 |
| `results/` | 冻结结构化结果；并非每个旧索引提到的图件都仍存在 |

旧索引中的 `90_临时归档/`、`backup_150dpi_json/` 在本轮前已不存在；本轮同时移除空 `logs/` 和 `checkpoints/`，不再列作资产。

## 实验入口

脚本采用无前缀导入，入口固定在 `research/experiments/`，避免根库 `models.py` 被 `research/models/` 同名包遮蔽：

```powershell
Set-Location 'D:\灵境制造\research\experiments'
$env:PYTHONPATH='02_LAM激光抑颤;03_PHM2010工具磨损;04_物理感知颤振;05_论文图件与表格;..;../..;../../engineering/python'
& '..\.venv\Scripts\python.exe' '02_LAM激光抑颤/thermal_sld_model.py'
```

运行 pytest 前清除 PYTHONPATH。科研测试：`cd research` 后用独立 `.venv/Scripts/python.exe -m pytest tests/ -q`。

## 解释边界

已有脚本和结果不能替代真实泛化与机制证据。历史巡检记录过 NumPy/Torch CFC 布局不一致、ODE/stage-2 NaN 问题；是否仍存在需按当前测试和真实数据复现，不沿用“其余全绿”的旧声明。本轮仅清理和关闭独立遗留入口，没有修复或扩大这些研究主张。
