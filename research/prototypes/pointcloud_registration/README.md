# pointcloud_registration — 叶片点云配准的可辨识性原型

**状态**：探索中（2026-10-20 建，2026-10-23 加入可辨识性判据）。**尚不构成论文贡献**。
**环境**：系统 Python 3.14（需要 cadquery / trimesh / scipy），**不要**用 `research/.venv`（无 cadquery）。

## 这里问的问题

不是"能不能把点云配上"，而是：**这份点云是否足以唯一确定位姿**。

对自对称（self-symmetric）叶片，压力面/吸力面 180° 翻转后形状与自身重合。此时
"翻转了"和"没翻转"两种位姿对数据的拟合程度相同，任何以 RMSE 取 argmin 的配准器
都只能在这两个等价解之间随机选择。历史脚本 `seed_sweep.py` 把"回到生成逆变换"
当成功、报告对称件 8/10——那 10 次里有 2 次是抛硬币抛输了，不是算法精度问题
（8/10 对 p=0.5 的双侧精确二项检验 p=0.055，不显著）。

## 三个量

| 量 | 含义 | 判定 |
|---|---|---|
| `symmetry_residual / sampling_floor` | G 映射后与自身点云的 RMSE ÷ 同形状双采样底噪 | ≈1 → 该形状在 G 下自对称，位姿**不由几何唯一决定**（设计阶段即可判定，不需要跑配准） |
| `Δ = RMSE(恒等) − RMSE(G⁻¹)` | 两个**无自由参数**位姿假设的残差差 | 单位为 mm；> 噪声量级才有信息 |
| `D = Δ / σ`，`P(Δ>0)` | σ 由配对自助法（200 次）给出；P 为"真位姿更优"的频率 | `D ≥ 2 且 P ≥ 0.95` → 可辨 |

**2026-10-23 实测两条要点**（详见 `docs/reports/identifiability_pointcloud_20261023.md`）：

1. 旧的"对称件 8/10"复测为 **13/20，p=0.263**——与抛硬币无法区分，不是精度。
2. `m=0` 时 `D` 恒为 0，补扫 50→800 点无效（几何不动点）；`m>0` 时 **`D ∝ √N`**，且**补扫区域比点数更重要**：同样 50 点，中弦带 `D`=31、前缘带 `D`=5。

## 脚本

| 文件 | 作用 |
|---|---|
| `blade_model.py` | NACA 4 位翼型沿 +Z 放样成叶片，导出 STEP（cadquery） |
| `registration_demo.py` | STEP→网格、表面采样、点到点 ICP、位姿误差 |
| `symmetry_probe.py` | 首个探针：翻转后从恒等起点跑 ICP，看是否被对称性骗 |
| `pca_coarse.py` | PCA 主方向 + 四符号候选 + ICP，取最低 RMSE |
| `seed_sweep.py` | 10 seed 重复上一个脚本，统计"回到真位姿"的比例（**旧口径，见上文警告**） |
| `identifiability_probe.py` | **本轮的判据脚本**：基线模式（四算例 × N seed）+ dose 模式（补扫点数 × 区域 vs 可辨度）+ **real 模式（任意真实点云自检，不需名义 CAD）** |
| `test_*.py` | 单元测试（CAD-free） |

## 运行

```bash
cd research/prototypes/pointcloud_registration
PY="C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe"

"$PY" identifiability_probe.py --mode baseline --seeds 20 --workers 5
"$PY" identifiability_probe.py --mode dose --seeds 6 --workers 3
# 真实点云（.pts/.txt/.xyz/.csv/.ply，逗号分隔多份）
"$PY" identifiability_probe.py --mode real --cloud "path/a.pts,path/b.ply"
"$PY" -m pytest test_pca.py test_registration.py test_identifiability.py -q   # 9 passed
```

## 真实数据

外部真实点云放 `data/external/`（当前只有两份**合成** smoke 文件，文件名带 `_probe`）。
公开叶片数据检索结论见 `docs/reports/public_blade_scan_datasets_20261023.md`：
唯一同时具备「实测 + 双侧叶面 + 名义 CAD」的是 WINSENT（DaRUS 10.18419/DARUS-3859，需申请）；
国内为华科无锡院「风电叶片测量点云数据」（NBSDC，需盖章）。

`--mode real` 的判据：在点云自身 PCA 框架下绕三条主轴各翻转 180°，比较 `翻转残差 / 点间距`。
≈1 即该轴自对称（位姿不可辨），显著 >1 即可辨；**分辨率上限 = 点间距**。

产物写在 `artifacts/identifiability_{baseline,dose}_<YYYYMMDD>/results.json`。

## 边界（不得越过）

- 全部点云是**合成**的（CAD 网格采样 + 0.02 mm 高斯噪声 + 10% 随机丢点），**没有真实扫描**。
  因此这里测的是"几何是否可辨"，不是"测量系统能否做到"。
- `D ≥ 2` 是工程阈值（约 2σ），不是统计定理；换噪声模型需重新标定。
- 补扫剂量—响应的结论只适用于**前缘带**这一种补扫策略；未比较其他区域。
