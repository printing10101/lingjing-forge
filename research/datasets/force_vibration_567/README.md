# force_vibration_567/ — 精密铣削铝：真实切削力 + 振动特征数据集

**来源**：GitHub 开放仓库 "Open Dataset: Vibration Features and Cutting Forces in Precision Milling of Aluminum Alloy"（`567ZYC/Open-Dataset-Vibration-Features-and-Cutting-Forces-in-Precision-Milling-of-Aluminum-Alloy`）
**许可**：CC BY 4.0（使用需按数据集说明引用原作者）
**内容**：五轴 CNC 精密槽铣铝合金（粗铣/半精/精加工）实测；振动 144×2 维时频特征经相关选择后保留与三轴切削力最相关的特征；三个 CSV 分别为 X/Y/Z 轴切削力 + 对应振动特征（各 207 行）。
**获取方式**：2026 网络受限期间经 codeload.github.com 下载，xlsx 转为 UTF-8 CSV 入库。

## 用途与边界（诚实标注）

- 可用于：切削力预测、振动-力关系建模、特征工程基准（真实数据）
- **不可用于** SLD 稳定性验证：本数据集**无切削参数列**（转速/切深/进给未提供）、**无稳定性标签**、**无温度**
- 不声称"实测稳定性数据"——那是 measured_stability/ 的职责

## 列说明（X 轴为例）

`X_vib_disp_mean, X_vib_disp_energy, X_vib_disp_rms, Y_vib_disp_margin_, Y_vib_vel_margin_f, Y_vib_disp_mean, Y_vib_acc_variance, Y_vib_acc_std, Y_vib_acc_rms, Y_vib_acc_energy, X-axial force`
（Y/Z 轴文件结构相同，目标列分别为 Y/Z 轴向力）
