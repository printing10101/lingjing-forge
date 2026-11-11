"""Separate observed frequency shifts from unobserved modal residues.

The compliance ratio here means the static modal flexibility, not a measured
rotating-tool peak FRF. Constant damping ratios are an explicit closure.
"""

import csv
import hashlib
from pathlib import Path

import numpy as np

from .coupled_milling import CoupledModes


def load_frequency_table(path, expected_sha256):
    path = Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("[来源漂移] 频率表哈希不匹配。建议操作：核对原图数字化记录。")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    speeds = np.array([float(row["rpm"]) for row in rows])
    frequencies = np.array([float(row["frequency_hz"]) for row in rows])
    if (len(speeds) < 2 or not np.isfinite(speeds).all()
            or not np.isfinite(frequencies).all() or np.any(speeds < 0)
            or np.any(np.diff(speeds) <= 0) or np.any(frequencies <= 0)):
        raise ValueError("[频率表错误] 转速须严格递增且频率为正。建议操作：重新核对坐标与单位。")
    return speeds, frequencies


def frequency_at_speed(speeds, frequencies, rpm):
    if not np.isfinite(rpm) or not speeds[0] <= rpm <= speeds[-1]:
        raise ValueError("[范围错误] 转速超出实测频率图范围。建议操作：不要外推缺失频响。")
    return float(np.interp(rpm, speeds, frequencies))


def rescale_modes(modes, frequency_ratio, static_compliance_ratio):
    """Keep zeta fixed and enforce both specified modal ratios exactly.

    H_new(s) = alpha * H_old(s / r), so omega_new = r*omega and
    V_new = r*sqrt(alpha)*V. This is a family of effective modal models;
    the frequency plot does not identify alpha or rotating damping.
    """
    if (not np.isfinite(frequency_ratio) or frequency_ratio <= 0
            or not np.isfinite(static_compliance_ratio) or static_compliance_ratio <= 0):
        raise ValueError("[模态变换错误] 频率与柔度比例须为正。建议操作：检查闭合假设。")
    return CoupledModes(
        np.asarray(modes.omega) * frequency_ratio,
        np.asarray(modes.damping).copy(),
        np.asarray(modes.participation) * frequency_ratio * np.sqrt(static_compliance_ratio),
    )


def static_compliance(modes):
    participation = np.asarray(modes.participation)
    return (participation / np.asarray(modes.omega)**2) @ participation.T
