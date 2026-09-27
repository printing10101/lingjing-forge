# real_validation/ — SLD/LNN 引擎的"实测数据"真实验证包（零设备方案）

> **解决的问题**：引擎（7 维切削参数 → 极限切深 a_lim）此前只被合成数据
> （"自采 6061-T6"、"NIST" 为 data_generator.py 中的合成占位）与自洽文献验证
> 检验过。审稿人会问："你的预测和**真实测量**对得上吗？"
> 本包提供三个**零设备、真实测量**验证通道。

## 三个真实数据源（全部零预算）

| 通道 | 数据 | 获取方式 | 验证能力 |
|---|---|---|---|
| **A. 公开颤振数据集** | Zenodo i-CNC 铣削颤振数据（record 15308467）：真实振动 + 颤振标注 | `ingest_icnc_zenodo.py` 下载 | 稳定性分类验证（主通道） |
| **B. 文献实测点** | 已发表 SLD 实验论文中的实测稳定/失稳点（带 DOI） | `ingest_literature_points.py` 录入 | 稳定性分类验证（主通道） |
| **C. Piecuch 2025** | 仓库已有真实铣削信号（ADOC/RDOC/硬度 + 128 特征 + 失效周期） | 已就位 | 补充实验（非 SLD 验证，见下） |

## 学术诚信硬约束（必须遵守）

1. **source + doi 必填**：每一行必须是可查证的实测数据，禁止编造。
2. **"SCHEMA-FIXTURE" 行是接口测试示例**，严禁当作实测数据用于论文/汇报。
3. **本包只做评估，不做 a_lim 回归训练**：实测数据通常只有二元稳定/失稳标签，
   连续边界值（a_lim_measured）仅在论文报告时存在。
4. **a_lim_physics 是模型预测通道**（Tlusty 解析模型输出），论文中必须与实测
   标签分开表述。
5. **不要把 PHM2010/NUAA 的"代理标签"混入本包**：它们的 a_lim 是从 Tlusty
   模型派生的（非实测），与真实测量是两回事。

## 网络受限环境下的数据获取实况（2026 实地测查）

| 源 | 可达性 | 结论 |
|---|---|---|
| Zenodo（i-CNC 15308467） | DNS 被污染（解析为 0.0.0.0），curl --resolve 可绕过，但速度仅 ~24 KB/s | 2.85GB 需 34 小时，**暂不可行**；结构为 2×4GB 原始振动 CSV（218 通道 + spindlespeed），无 ap/ae/硬度，颤振标注为 AI 检测生成 |
| GitHub / codeload | 可达 | 搜到真实小数据集：`datasets/force_vibration_567/`（精密铣削铝，力+振动特征，207 行，CC BY 4.0，已入库） |
| UCI / 国内平台（魔搭、和鲸、百度 AI Studio） | UCI ；魔搭 API 500 | NASA milling 为**磨损数据**（非颤振），不适用于 SLD 验证 |
| **文献实测点（通道 B）** | **知网/百度学术/校图书馆 ** | **主通道**——见 `LITERATURE_POINTS_CHECKLIST.md` |

**结论：实测稳定性数据的主通道 = 文献实测点录入（无需下载大文件）。**

## 快速开始

```bash
cd research/experiments

# 1) 校验现有（fixture）数据
../.venv/Scripts/python.exe real_validation/ingest_literature_points.py --validate

# 2) 录入文献实测点（论文报告了稳定/失稳的 (n, ap, ...) 试验）
../.venv/Scripts/python.exe real_validation/ingest_literature_points.py

# 3) （可选）下载 Zenodo i-CNC 颤振数据
../.venv/Scripts/python.exe real_validation/ingest_icnc_zenodo.py --dry-run
../.venv/Scripts/python.exe real_validation/ingest_icnc_zenodo.py
```

## 验证协议（论文可报告的指标）

用 `evaluate_stability_classification` 将任何 a_lim 预测器（LNN / PINN /
Tlusty 解析模型）的稳定性判定与实测标签对比：

```python
from real_validation import MeasuredStabilityPointsDataset, evaluate_stability_classification

ds = MeasuredStabilityPointsDataset()
print(ds.stability_summary())

def my_predictor(features_np):
    # 例：调用已训练的引擎 a_lim 预测器（接收 [N,7] → 返回 [N] a_lim mm）
    return model_predict_a_lim(features_np)

metrics = evaluate_stability_classification(my_predictor, ds)
print(metrics)  # accuracy / balanced_accuracy / mcc / roc_auc
```

**报告规则**：
- 指标：Accuracy + Balanced Accuracy + MCC（+ ROC-AUC 当两类样本均存在）
- 必须同时报告样本数、稳定/失稳分布、数据来源清单（含 DOI）——审稿人可核验
- 与合成数据结果并列呈现时，**必须明确区分**"实测验证"与"仿真验证"两节

## 数据文件位置

| 文件 | 说明 |
|---|---|
| `datasets/measured_stability/measured_stability_points.csv` | schema 数据（当前为 fixture 示例） |
| `datasets/icnc_chatter/` | Zenodo i-CNC 原始数据（下载后） |

## 与 Piecuch 2025 的关系（通道 C）

Piecuch 2025（仓库已有）是真实铣削数据，但**无稳定性标签**（只有失效周期），
且缺主轴转速/进给/齿数。它不能验证 SLD 引擎的 a_lim 预测，只能作为
"真实信号特征 → 状态"补充实验（如 CycleToFailure 预测），论文中应明确标注
为补充实验，不得混入稳定性验证。

## 下一步（给引擎的接入）

1. 录入 ≥20 个文献实测点（至少覆盖 2 篇不同论文、2 种材料）→ 第一个可报告
   的"真实数据验证"指标。
2. 下载并转换 i-CNC 数据 → 扩大样本量（数百条振动片段 → 颤振标注）。
3. 在论文主对比中新增 "Measured SLD" 一节，用本包指标替换合成占位数据的
   "自采 6061-T6 / NIST" 条目。
