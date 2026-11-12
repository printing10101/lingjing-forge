"""实测评估共享的极限切深判定；临界点保守记为非稳定。"""

import numpy as np


def classify_stability(axial_depth, limiting_depth):
    """返回 1=稳定、0=非稳定；拒绝无法解释的边界与非有限输入。"""
    ap = np.asarray(axial_depth, dtype=np.float64)
    limit = np.asarray(limiting_depth, dtype=np.float64)
    if not np.all(np.isfinite(ap)) or not np.all(np.isfinite(limit)):
        raise ValueError("[稳定性输入错误] 切深和极限必须为有限数。建议操作：核查预测输出与输入单位。")
    if np.any(ap < 0) or np.any(limit <= 0):
        raise ValueError("[稳定性输入错误] 切深不得为负，极限必须为正。建议操作：核查数据与模型适用域。")
    return np.less(ap, limit).astype(np.int64)
