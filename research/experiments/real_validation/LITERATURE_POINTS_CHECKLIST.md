# 文献实测点录入清单（通道 B · 用户执行版）

> 网络受限环境下，**实测稳定性数据的主通道**：从已发表论文中录入实测稳定/失稳点。
> 看论文用知网/百度学术/学校图书馆数据库（国内可达），录入用 `ingest_literature_points.py`。
> 数据值必须来自论文原文（表格或图件数字化），**严禁凭记忆填写**。

## 推荐起始论文（经典 SLD 实验验证，均含实测稳定/失稳数据）

| 论文 | 为什么选它 | 预计可录入点数 |
|---|---|---|
| **Altintas & Budak 1995**, "Analytical prediction of stability lobes in milling", CIRP Annals 44(1) | SLD 奠基论文，含铣削实测稳定性边界验证 | 15–30 点（图件） |
| **Budak & Altintas 1998**, "Analytical prediction of chatter stability in milling, Part I/II", ASME J. Manuf. Sci. Eng. 120(1) | 双篇完整实验验证，稳定/失稳点标注清晰 | 20–40 点 |
| **Insperger, Stépán, et al. 2003**, "Stability of up-milling and down-milling, Part 1/2", Int. J. Mach. Tools Manuf. 43(1) | 逆铣/顺铣对比实验，含不稳定区域图 | 20–30 点 |
| **Faassen et al. 2003**, "Prediction of regenerative chatter...high-speed milling", IJMTM 43(14) | 高速铣削实测稳定性点 | 10–20 点 |
| **Gradišek et al. 2005**, "On stability prediction for milling", IJMTM 45(12-13) | 半离散法验证 + 实验 | 10–20 点 |

> 注：上表 DOI 请在下载论文后从原文填写（schema 要求 doi 字段可查证）。这些论文在知网/百度学术/校图书馆数据库基本都能检索到。

## 录入工作流（每篇论文约 20–30 分钟）

1. **下载论文 PDF**（知网/百度学术/校图书馆），找到"Stability Lobe / Experimental Validation / 实验验证"章节
2. **提取每个试验点**：论文会报告"在 n=X rpm、ap=Y mm 下稳定/发生颤振"，记录：
   - n_rpm（转速）、ap_mm（轴向切深）、ae_mm（径向切宽）、feed_mm_per_tooth（每齿进给）
   - tool_diameter_mm、num_teeth（刀具几何，论文材料表里找）
   - hardness_hb（工件材料硬度，查材料手册或论文）
   - stable（实测结果 0/1）
   - a_lim_measured_mm（若论文给出边界值；图件上的边界交点可数字化）
3. **图件数字化**（论文只给图没给表时）：
   - 用开源工具 WebPlotDigitizer（离线可用）或 ImageJ 从稳定性叶瓣图提取 (n, ap) 坐标点
   - 在 source 字段注明"（digitized from Fig.X）"
4. **录入**：
   ```bash
   cd research/experiments
   ../.venv/Scripts/python.exe real_validation/ingest_literature_points.py --validate
   ../.venv/Scripts/python.exe real_validation/ingest_literature_points.py   # 交互式
   ```
5. **每篇录完后跑验证**：
   ```python
   from real_validation import MeasuredStabilityPointsDataset, evaluate_stability_classification
   ds = MeasuredStabilityPointsDataset()
   print(ds.stability_summary())
   metrics = evaluate_stability_classification(lambda X: ds.a_lim_physics, ds)
   print(metrics)
   ```
   先看 Tlusty 解析模型本身的基线（Accuracy/MCC），再换 LNN 引擎预测器对比。

## 质量与诚信检查（录入前自查）

- [ ] 每个点都有 source + doi
- [ ] 数值逐字来自论文（或注明 digitized）
- [ ] 材料/刀具参数与论文实验设置一致（不是猜的）
- [ ] 不同论文的材料不同时，hardness_hb 填各论文实际值
- [ ] 图件数字化时注明 Fig 编号
- [ ] 至少覆盖 2 篇论文、2 种材料才算"可信的第一批"

## 目标

- **第一批（本周）**：1 篇论文（Altintas & Budak 1995），15–30 点 → 第一个可报告的实测验证指标
- **第二批（下周）**：再录 1–2 篇（不同材料）→ 累计 40–70 点 → 论文"Measured SLD"一节的数据基础
