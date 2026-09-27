"""
Piecuch Nature SD 2025 数据集预处理管线

输入: FeatureAndMetadata_Milling.csv (968 cycles × 131 features)
输出: DL-LNN 兼容的 7 维特征矩阵 + 颤振代理标签

映射关系:
    DL-LNN 7维 [n, f, ap, ae, H, D, z]
    数据集中可以获取: ap=ADOC, ae=RDOC, H=HardnessMean
    需要估计: n(主轴转速), f(进给), D(刀具直径), z(齿数)

标签构造:
    原始数据集不含颤振标签。利用振动 RMS 与理论稳定极限的相对偏差
    构造代理标签: chatter_score = vibration_RMS / f(ADOC, RDOC, Hardness)
    高 RMS + 大 ADOC = 高颤振概率

输出:
    piecuch_dlnn_features.csv: 7 维归一化特征 + chatter_label
"""

import pandas as pd
import numpy as np
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datasets", "piecuch_2025")
INPUT_CSV = os.path.join(DATA_DIR, "FeatureAndMetadata_Milling.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "piecuch_dlnn_features.csv")

# 1. 加载与清洗

df = pd.read_csv(INPUT_CSV, sep=";", decimal=",", encoding="utf-8", skiprows=[0])
print(f"[1] 加载数据: {df.shape[0]} 行 × {df.shape[1]} 列")

# 振动标准差列（8 通道）
ACCEL_STD_COLS = [
    "Accelerometer - Spindle +Y - std",
    "Accelerometer - Spindle -Z - std",
    "Accelerometer - Spindle -X - std",
    "Accelerometer - X Driving axle +Z - std",
    "Accelerometer - X Driving axle -X - std",
    "Accelerometer - Y Driving axle +Z - std",
    "Accelerometer - Y Driving axle +Y - std",
    "Accelerometer - Y Driving axle -X - std",
]

# 元数据列
META_COLS = [
    "FileName",
    "NumberOfCycle",
    "SampleIndex",
    "TollIndex",
    "MillingToolType",
    "ADOC",
    "RDOC",
    "HardnessMean",
    "ToolHolderLength",
    "CycleToFailure",
    "CycleToFailureNormalized",
]

# 2. 构造 7 维 DL-LNN 输入

n_samples = len(df)

# 主轴转速 n (rpm) — 估计值
# 来源: Nature 论文描述 Haas VF-1, 42CrMo4, D10 4-齿端铣刀
# 标准切削参数: n≈4000 rpm, f≈0.1 mm/齿
SPINDLE_SPEED = np.full(n_samples, 4000.0, dtype=np.float32)  # rpm
FEED_PER_TOOTH = np.full(n_samples, 0.1, dtype=np.float32)  # mm/齿
TOOL_DIAMETER = np.full(n_samples, 10.0, dtype=np.float32)  # mm
NUM_TEETH = np.full(n_samples, 4, dtype=np.float32)  # 齿数

# 轴向切深 a_p (mm) — ADOC
ap = df["ADOC"].values.astype(np.float32)  # [5, 10] mm

# 径向切宽 a_e (mm) — RDOC
ae = df["RDOC"].values.astype(np.float32)  # [4.5, 8] mm

# 材料硬度 H (HRC) — HardnessMean
H = df["HardnessMean"].values.astype(np.float32)  # [35, 42] HRC

# 特征矩阵 [N, 7]
features_raw = np.column_stack(
    [
        SPINDLE_SPEED,  # n (rpm)
        FEED_PER_TOOTH,  # f (mm/齿)
        ap,  # a_p (mm)
        ae,  # a_e (mm)
        H,  # H (HRC)
        TOOL_DIAMETER,  # D (mm)
        NUM_TEETH,  # z
    ]
)

# 3. 构造颤振代理标签

# 方法：计算 8 通道加速的 RMS 的均方根，作为"振动强度"
accel_std = df[ACCEL_STD_COLS].values  # [N, 8]
vibration_rms = np.sqrt(np.mean(accel_std**2, axis=1))  # [N]

# 颤振代理标签: vibration_rms / a_p (切深越大+振动越大=越可能颤振)
# 归一化到 [0, 1]
raw_score = vibration_rms * ap  # 振动×切深
chatter_label = (raw_score - raw_score.min()) / (raw_score.max() - raw_score.min() + 1e-8)

print(f"[2] 振动 RMS 范围: [{vibration_rms.min():.1f}, {vibration_rms.max():.1f}]")
print(f"[2] 颤振标签范围: [{chatter_label.min():.3f}, {chatter_label.max():.3f}]")

# 4. 归一化到 [0, 1]（与 DL-LNN 训练数据格式一致）

# 参考归一化常量（与 data_generator_v2.py 一致）
NORM_SCALES = np.array([10000.0, 0.5, 10.0, 8.0, 200.0, 20.0, 6.0], dtype=np.float32)

features_normalized = features_raw / NORM_SCALES
features_normalized = np.clip(features_normalized, 0.0, 1.0)

# 5. 保存

output = pd.DataFrame(
    features_normalized,
    columns=["n", "f", "ap", "ae", "H", "D", "z"],
)
output["chatter_label"] = chatter_label
output["cycle_id"] = df["FileName"].values
output["vibration_rms"] = vibration_rms
output["ap_raw"] = ap
output["ae_raw"] = ae

output.to_csv(OUTPUT_CSV, index=False)
print(f"[3] 保存: {OUTPUT_CSV}")
print(f"    形状: {output.shape}")
print(f"    列: {list(output.columns)}")
print(f"    颤振标签统计: mean={chatter_label.mean():.3f}, std={chatter_label.std():.3f}")
print(f"    高颤振样本 (>0.8): {int((chatter_label > 0.8).sum())} / {n_samples}")
print(f"    低颤振样本 (<0.2): {int((chatter_label < 0.2).sum())} / {n_samples}")

# 6. 与合成数据的交叉验证建议

print(f"\n{'=' * 60}")
print(f"使用建议:")
print(f"  1. 将此 CSV 作为 DL-LNN 的 '真实数据验证集'")
print(f"  2. 用合成数据 (Tlusty ZOA) 预训练模型")
print(f"  3. 用此数据集的 80% (774 样本) 微调")
print(f"  4. 用 20% (194 样本) 评估 sim→real 迁移增益")
print(f"{'=' * 60}")
