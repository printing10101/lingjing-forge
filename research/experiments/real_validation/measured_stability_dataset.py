"""实测稳定性点数据集 + 稳定性分类验证工具。

设计原则（学术诚信）：
    1. 本数据集只做**评估/验证**，不做 a_lim 回归训练——实测数据通常只有
       二元稳定/失稳标签，没有连续边界值（有边界值的行会保留 a_lim_measured）。
    2. 输入特征来自真实测量记录的切削参数（n, f, ap, ae, H, D, z），
       与引擎 config.py 的 input_dim=7 严格对齐（build_physics_features_7d）。
    3. a_lim_physics 通道是**模型预测**（TlustyAnalyticalModel 在相同参数下
       的解析预测），明确标注为模型输出，不是测量值。
    4. 使用方式：evaluate_stability_classification() 将任何 a_lim 预测器的
       稳定性判定（ap > a_lim_pred）与实测 stable 标签对比，输出
       Accuracy / BalancedAccuracy / MCC / ROC-AUC —— 这是论文可报告的
       "真实数据验证"指标，且不依赖任何设备。

运行前提（与 experiments/ 其他脚本一致）：
    cd research/experiments
    用 research/.venv 的 python（含 pandas / sklearn / torch）
"""

import os
import sys
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset

# 兼容两种导入上下文：experiments/ 在 sys.path（脚本运行）或不在（包导入）
try:
    from data_generator import build_physics_features_7d, TlustyAnalyticalModel
except ImportError:  # pragma: no cover
    _EXP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _EXP_DIR not in sys.path:
        sys.path.insert(0, _EXP_DIR)
    from data_generator import build_physics_features_7d, TlustyAnalyticalModel

from .schema import FEATURE_KEY_COLUMNS, NUMERIC_COLUMNS, load_rows, to_float, validate_schema

DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "datasets",
    "measured_stability",
    "measured_stability_points.csv",
)


class MeasuredStabilityPointsDataset(Dataset):
    """实测稳定性点数据集。

    __getitem__ 返回 (features, a_lim, a_lim_physics) 三元组，
    与其他数据集类保持接口一致：
        - features:      [7] float32（build_physics_features_7d）
        - a_lim:         [1] float32 —— 实测边界值；论文未报告时为 NaN
        - a_lim_physics: [1] float32 —— Tlusty 解析模型预测（模型通道，非测量）

    附加访问器：
        - .stability:  np.ndarray[int] 实测 0/1（主验证通道）
        - .sources:    list[str] 每行来源（含 doi）
        - .ap_mm:      np.ndarray 试验切深（用于稳定性判定 ap > a_lim_pred）
    """

    def __init__(
        self,
        csv_path: Optional[str] = None,
        tlusty_stiffness: float = 0.9e6,
        tlusty_modal_mass: float = 95.0,
        tlusty_damping_ratio: float = 0.048,
    ):
        super().__init__()
        self.csv_path = csv_path or DEFAULT_CSV
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(
                f"实测稳定性数据文件不存在: {self.csv_path}\n"
                "请先运行 ingest_icnc_zenodo.py / ingest_literature_points.py 生成数据，"
                "或检查 datasets/measured_stability/ 目录。"
            )
        self._rows = load_rows(self.csv_path)

        ok, problems = validate_schema(self._rows)
        if not ok:
            raise ValueError("实测稳定性数据 schema 校验失败:\n  " + "\n  ".join(problems))

        # 特征与标签
        self.n_rpm = np.array([to_float(r, "n_rpm") for r in self._rows], dtype=np.float32)
        self.feed = np.array([to_float(r, "feed_mm_per_tooth") for r in self._rows], dtype=np.float32)
        self.ap = np.array([to_float(r, "ap_mm") for r in self._rows], dtype=np.float32)
        self.ae = np.array([to_float(r, "ae_mm") for r in self._rows], dtype=np.float32)
        self.hardness = np.array([to_float(r, "hardness_hb") for r in self._rows], dtype=np.float32)
        self.diameter = np.array([to_float(r, "tool_diameter_mm") for r in self._rows], dtype=np.float32)
        self.teeth = np.array([to_float(r, "num_teeth") for r in self._rows], dtype=np.float32)
        self.stability = np.array([int(r.get("stable", "0")) for r in self._rows], dtype=np.int64)
        self.a_lim_measured = np.array([to_float(r, "a_lim_measured_mm") for r in self._rows], dtype=np.float32)
        self.sources = [f"{r.get('source', '')} | {r.get('doi', '')}" for r in self._rows]
        self.materials = [r.get("material", "") for r in self._rows]

        # 检查特征列完整性（2026-09-15 修复：原检查只覆盖 3 列，导致 NaN 静默流入）
        # 必填列：缺失直接拒绝——它们进入 7 维特征且物理模型必需
        for name, arr in [
            ("n_rpm", self.n_rpm),
            ("ap_mm", self.ap),
            ("ae_mm", self.ae),
            ("hardness_hb", self.hardness),
            ("tool_diameter_mm", self.diameter),
            ("num_teeth", self.teeth),
        ]:
            if np.isnan(arr).any():
                raise ValueError(
                    f"列 '{name}' 存在 NaN——它是 7 维特征的必需项，缺失会使物理模型输出 NaN。"
                    "请补齐数据或删除对应行。"
                )
        # feed_mm_per_tooth：论文常不报告该值。缺失时下游必须走 Tlusty 的默认分支，
        # 且【绝不能】把 NaN 传进物理模型（见下方 feed_arg 处理）。
        self.feed_missing = bool(np.isnan(self.feed).any())
        if self.feed_missing:
            import warnings

            warnings.warn(
                "列 'feed_mm_per_tooth' 存在缺失：物理模型将使用其默认进给（0.25 mm/tooth）；"
                "但 7 维特征的第 2 维会是 NaN——任何依赖该特征的模型需自行处理缺失值。",
                stacklevel=2,
            )

        # 7 维特征（与 config.input_dim=7 对齐）
        self.features = build_physics_features_7d(
            spindle_speed=self.n_rpm,
            feed_rate=self.feed,
            axial_depth=self.ap,
            radial_depth=self.ae,
            hardness=self.hardness,
            tool_diameter=self.diameter,
            num_teeth=self.teeth,
        )

        # 模型预测通道（明确标注为模型输出，非测量）
        self._tlusty = TlustyAnalyticalModel(
            stiffness=tlusty_stiffness,
            modal_mass=tlusty_modal_mass,
            damping_ratio=tlusty_damping_ratio,
        )
        # 2026-09-15 修复（根因）：feed 缺失时必须传 None，不能传 NaN。
        # TlustyAnalyticalModel.compute_limiting_depth 对 feed_rate=None 会走默认分支
        # （实测 feed_rate=None -> a_lim=13.469），而对 feed_rate=NaN 会把 NaN 传播出去
        # （实测 -> a_lim=NaN），进而使判定式 (ap > NaN) 恒为 False，全部误判为"失稳"。
        feed_arg = None if self.feed_missing else self.feed
        with np.errstate(all="ignore"):
            self.a_lim_physics = self._tlusty.compute_limiting_depth(
                self.n_rpm,
                hardness=self.hardness,
                tool_diameter=self.diameter,
                num_teeth=self.teeth,
                feed_rate=feed_arg,
                radial_depth=self.ae,
            ).astype(np.float32)
        # 禁止 NaN 进入稳定性判定（fail-fast，避免"指标虚高"这类静默失真）
        if np.isnan(self.a_lim_physics).any():
            bad = np.where(np.isnan(self.a_lim_physics))[0].tolist()
            raise ValueError(
                "Tlusty 物理模型输出了 NaN（行索引 %s）——NaN 会使判定式恒为 False、"
                "把全部样本误判为失稳并让 accuracy 虚高。请检查这些行的输入参数范围。"
                % bad[:20]
            )

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.features[idx])
        a_lim = torch.from_numpy(np.array([self.a_lim_measured[idx]], dtype=np.float32))
        a_lim_physics = torch.from_numpy(np.array([self.a_lim_physics[idx]], dtype=np.float32))
        return features, a_lim, a_lim_physics

    # 附加访问器
    def rows_with_measured_boundary(self) -> np.ndarray:
        """有实测边界值 a_lim_measured 的行索引（可用于回归验证）。"""
        return np.where(~np.isnan(self.a_lim_measured))[0]

    def stability_summary(self) -> Dict[str, int]:
        """稳定/失稳样本统计。"""
        return {
            "total": int(len(self._rows)),
            "stable": int((self.stability == 1).sum()),
            "unstable": int((self.stability == 0).sum()),
        }


def evaluate_stability_classification(
    predict_a_lim: Callable[[np.ndarray], np.ndarray],
    dataset: MeasuredStabilityPointsDataset,
    return_details: bool = False,
) -> Dict[str, float]:
    """将任意 a_lim 预测器与实测稳定性标签对比（分类验证，主验证通道）。

    Args:
        predict_a_lim: 接收 [N,7] 特征矩阵，返回 [N] 预测极限切深 (mm) 的可调用对象。
        dataset: MeasuredStabilityPointsDataset 实例。
        return_details: 是否返回混淆矩阵等细节。

    Returns:
        metrics 字典：accuracy / balanced_accuracy / mcc / roc_auc /
        n_stable_true / n_unstable_true（roc_auc 仅在两类样本均存在时计算）。
    """
    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        confusion_matrix,
        matthews_corrcoef,
        roc_auc_score,
    )

    y_true = dataset.stability.astype(int)
    a_lim_pred = np.asarray(predict_a_lim(dataset.features), dtype=np.float32).reshape(-1)
    if len(a_lim_pred) != len(y_true):
        raise ValueError(f"predict_a_lim 返回长度 {len(a_lim_pred)} != 样本数 {len(y_true)}")
    # 2026-09-15 修复（判定方向）：物理上 a_lim 是【极限切深】——
    # 切深 ap 低于极限切深 a_lim 才是稳定，超过则发生颤振。
    # 原实现写作 (ap > a_lim) 并把结果标为"稳定"，方向与物理相反，
    # 直接导致所有模型（含 Tlusty 基线）的 MCC 为负值。
    y_pred = (dataset.ap < a_lim_pred).astype(int)

    metrics: Dict[str, float] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "mcc": float(matthews_corrcoef(y_true, y_pred)),
        "n_stable_true": int((y_true == 1).sum()),
        "n_unstable_true": int((y_true == 0).sum()),
    }
    if len(np.unique(y_true)) == 2 and len(np.unique(y_pred)) == 2:
        try:
            metrics["roc_auc"] = float(roc_auc_score(y_true, y_pred))
        except ValueError:
            metrics["roc_auc"] = float("nan")
    else:
        metrics["roc_auc"] = float("nan")

    if return_details:
        metrics["confusion_matrix"] = confusion_matrix(y_true, y_pred).tolist()
        metrics["n_stable_pred"] = int((y_pred == 1).sum())
        metrics["n_unstable_pred"] = int((y_pred == 0).sum())
    return metrics
