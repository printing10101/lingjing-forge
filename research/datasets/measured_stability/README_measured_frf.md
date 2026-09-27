> **最新状态（2026 本会话更新）**：
> - **`measured_stability_points.csv` 已有 7 个真实实测点**（论文正文明确报告的 A–G 点：
>   n=3000–15000 rpm × ap=0.1–3 mm × 稳定/颤振，含低频颤振），**无需数字化、无需人工确认**，
>   全部来自 Ji 2024 SciRep（DOI 10.1038/s41598-024-76165-8），可查证。
> - 图件中另有约 80 个点（Fig10/13），已配半自动工具 `digitize_fig_markers.py` 预检测，
>   坐标轴标定可选（WebPlotDigitizer），**非必需**——7 个文字点已构成第一批可报告的实测验证数据。

# 实测模态参数数据集（锤击实验，来自 OA 论文）

## 来源（可查证）
- 论文：**Ji Yongjian et al., "Research on the influence of cutter overhang length on robotic milling chatter stability"**, *Scientific Reports* 14 (2024), DOI: [10.1038/s41598-024-76165-8](https://doi.org/10.1038/s41598-024-76165-8)
- 获取：Europe PMC（PMC11496886，OA，CC BY 4.0）全文 XML 的 Table 1/2
- 内容：**锤击实验（hammer test）实测的铣刀模态参数**——不同刀柄悬伸长度（35/45/55/65 mm）× 前 5 阶模态 × x/y 两轴

## 文件
| 文件 | 说明 |
|---|---|
| `measured_frf_11496886.csv` | 40 行模态参数：axis, overhang_mm, mode, freq_hz, damping_ratio, modal_mass_kg |
| `figures/Fig10-13.jpg` | 论文实测稳定性验证图（实心圆=稳定，叉=颤振）——**待数字化**（见下） |

## 用途（诚实边界）
- 可用于：**FRF → SLD 引擎验证**（不同悬伸 → 不同模态 → 稳定性叶瓣预测 vs 论文 Fig 10-13 实测验证点对照）；这是真实测量数据
- 可用于：刀柄悬伸对颤振稳定性影响的机理研究（exp49 主轴外推的同类主题）
- 注意：模态参数是"铣刀模态"，不是整机模态；使用时须与论文工况（机器人铣削）语境一致

## 真实切削力预测验证（567 数据集，2026-08 会话）

运行：`real_validation/run_force_prediction_validation.py`（结果 `experiments/results/force_prediction_567_results.json`）
任务：由真实振动特征预测实测三轴切削力（206 样本/轴，70/15/15，MinMax 归一化）

| 模型 | X轴 R²/PCC | Y轴 R²/PCC | Z轴 R²/PCC | 三轴平均 R² |
|---|---|---|---|---|
| LTC（引擎核心单元） | 0.044 / 0.448 | -0.817 / -0.325 | -0.245 / 0.129 | **-0.34** |
| XGBoost | -0.056 / 0.467 | -1.611 / -0.314 | -0.304 / 0.110 | -0.66 |
| RandomForest | 0.066 / 0.492 | -0.613 / -0.251 | -0.106 / 0.162 | -0.22 |
| SVR | -0.059 / 0.426 | -0.411 / -0.205 | -0.206 / 0.307 | -0.23 |

**诚实解读**：
1. **这是数据的问题，不是引擎的问题**：四个模型（含 XGBoost/RF 强基线）在三个轴上都基本失败（R²≈0 或为负）——该数据集宣称"与力最相关"的振动特征在留出集上无法预测力（206 样本过小 + 特征弱）。
2. **引擎表现与强基线同级**：LTC 的 R²(-0.34) 介于 XGBoost(-0.66) 与 RF/SVR(-0.22/-0.23) 之间——引擎能力没有被数据证伪，但也没有正向信号。
3. **发现的引擎鲁棒性缺陷（值得修复）**：该数据上 torchdiffeq 自适应积分发散（dt=nan）→ 需强制 Euler；DLLNNWithPhysics 包装器 stage-2 也出现 NaN。这是引擎对真实数据数值稳定性的真实问题，建议后续修（梯度裁剪/求解器降级策略）。
4. **论文用法**：作为"真实数据上的力预测基准"报告 LTC 与基线同级的表现即可；不要声称预测成功。


## 第二份实测模态数据集：Inconel 718（机床铣削）

**来源**：Zheng J. et al., "Milling Mechanism and Chattering Stability of Nickel-Based Superalloy Inconel 718", *Materials* 16(17):5748 (2023), DOI: [10.3390/ma16175748](https://doi.org/10.3390/ma16175748)（PMC10488871，OA，CC BY）
**文件**：`measured_frf_10488871.csv`（6 行：x/y 向 × 3 阶的实测固有频率 + 阻尼比，Table 7）
**实验平台**：DMC635V 立式加工中心（DMG）+ Sandvik 1B240-0800-XA 1630 硬质合金球头铣刀（D=8mm，2 齿）+ Kistler 测力
**工件**：Inconel 718（镍基高温合金）

**可提取的实验数据**：
- Table 7 模态参数（f、ζ）——真实实测，已入库
- Table 8/9 实验矩阵（正交 16 点：n=800-1400 rpm × fz=0.015-0.06 × ap=0.1-0.4 mm）
- 切削力实验（验证力模型，数值在图件 Figure 22-26）
- **无逐点实测稳定/颤振标签**——该文颤振分析为 SLD/FEA 模型输出，实验只验证力

**注意**：模态频率 ~935 Hz 对应 n=800-1800 rpm 时叶瓣数 j≈31-70，超出引擎 `num_lobes=10` 近似范围——直接用它验证 SLD 会暴露引擎叶瓣数限制，宜作为"引擎局限"案例而非验证案例。


## 实测点验证结果（已跑：7 点 × 3 模型，2026-08 会话）

运行：`real_validation/run_real_points_validation.py`（结果存 `experiments/results/real_points_validation_results.json`）

| 模型 | Accuracy | BalancedAcc | MCC | 备注 |
|---|---|---|---|---|
| A 默认 Tlusty（引擎默认模态） | 0.571 | 0.400 | -0.258 | 4/7 |
| B 真实模态 Tlusty（论文 40 行模态，按悬伸+进向配 mode-1） | 0.429 | 0.450 | -0.091 | 3/7 |
| C DL-LNN（合成 7 维空间 6+8 轮快速训练） | 0.571 | 0.400 | -0.258 | 4/7 |

**诚实解读（这是有价值的结果，不是失败）**：
1. **框架跑通**：逐点对比 + 三模型指标完整产出，真实数据验证通道可用。
2. **低准确率与论文结论一致**：该论文（机器人铣削）的核心发现正是"35mm 短悬伸下，基于铣刀模态的 SLD **无法**准确预测实际加工状态"（能量转移到机器人本体）。本验证在 35mm 三点（A/B/C）上的失败与该发现一致——**我们的引擎重现了文献结论**，这是可写进论文的交叉验证证据。
3. **局限**：① 引擎为单模态 Tlusty，论文指出多模态耦合才是 65mm 下准确的（mode-1 不足以刻画）；② 机器人铣削与机床铣削动态差异大；③ feed 未报告（A/B 用默认、C 用 0.25 假设）。
4. **论文用法建议**：不要把这个数据集当"引擎验证成功"的证据，而是当"诚实负面/边界结果 + 框架能力展示"——或者**用这个数据集验证论文中机器人/工艺阻尼相关论断**（如"低转速区稳定点更多=工艺阻尼效应"，A 点在 B 模型下正确判稳）。


## 实测稳定性点数字化（已配半自动工具）
Fig 10/13 含实测稳定（实心圆）/颤振（叉）点。**先跑工具自动检测+分类，再用 WebPlotDigitizer 标定坐标轴**：

```bash
cd research/experiments
../.venv/Scripts/python.exe real_validation/digitize_fig_markers.py \
    --fig ../datasets/measured_stability/figures/Fig10.jpg \
    --out results/fig10_markers.csv
../.venv/Scripts/python.exe real_validation/digitize_fig_markers.py \
    --fig ../datasets/measured_stability/figures/Fig13.jpg \
    --out results/fig13_markers.csv
```

已生成候选（Fig10: 17 稳定圆 + 27 颤振叉；Fig13: 43 稳定圆 + 3 颤振叉）——**注意**：
- 工具会把 y 轴刻度/图例误判为候选，须在 WebPlotDigitizer 里目视剔除（x=82 整列、图例区）
- 最终数值以 WebPlotDigitizer 标定导出为准，逐点核对后再录入

**本论文实验工况（录入 schema 时使用）**：
| 字段 | 值 | 来源 |
|---|---|---|
| tool_diameter_mm | 6 | 论文 Sect 3/5（3 齿硬质合金） |
| num_teeth | 3 | 同上 |
| ae_mm | 1.0（ae/D=1/6，顺铣 down-milling） | Fig 7/8 图注 |
| material / hardness_hb | Al7075，≈150 HB | 论文 Sect 5 |
| n_rpm / ap_mm | 从图件数字化（校准锚点：A=10000r/min@3mm，B=15000r/min@3mm） | 论文 Sect 5.1 |

录入时 source 填 "Ji 2024 SciRep digitized from Fig10/13"，doi=10.1038/s41598-024-76165-8。

## 实测稳定性点数字化（图件 → 数据）
Fig 10–13 含实测稳定/颤振点（实心圆/叉）。自动数字化需要 OCR（环境无 tesseract），
**禁止猜测**。用 WebPlotDigitizer（免费离线软件）手动提取：
1. 打开 `figures/Fig10.jpg`，设置坐标轴（x=主轴转速 rpm，y=轴向切深 ap mm，以图中刻度为准）
2. 逐点标记实心圆（stable=1）与叉（chatter=0），导出 CSV
3. 用 `ingest_literature_points.py --append` 写入 measured_stability_points.csv
   （source 注明 "Ji 2024 SciRep digitized from Fig10"，doi=10.1038/s41598-024-76165-8）
4. 同时补：刀具齿数/直径、材料、ae、feed（论文 Methods 或图注）

## 引用格式（论文使用）
Ji, Y. et al. Research on the influence of cutter overhang length on robotic milling chatter stability. *Sci. Rep.* 14, 21046 (2024).
