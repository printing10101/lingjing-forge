# -*- coding: utf-8 -*-
"""
Step 4：物理模型失效预警器（152 点，LOOCV + 置换检验）。

任务：给定工况与模型自身输出（开切前全部可算），预测物理模型这次会不会错、往哪边错。
  T1 model_error   （pred != actual，42 正例）
  T2 aggressive    （预测稳定实为颤振，25 正例——工程上最危险）
  T3 conservative  （预测颤振实为稳定，17 正例）

特征（预注册，7 个，全部无标签泄漏）：
  n_krpm, ap, overhang, is_y, margin_signed, res_dist, zeta_gov

协议：
  - LOOCV（152 折），标准化只在训练折内拟合（Pipeline）；
  - 逻辑回归(L2, C=1) + 深度2决策树（可解释规则）；
  - 基线：永远信任模型（预测无错误）；
  - 显著性：标签置换 30 次 → 零分布 vs 实测 balanced accuracy。
"""
import csv, io, json, sys
import numpy as np

sys.path.insert(0, r"D:\灵境制造\research\experiments")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.metrics import balanced_accuracy_score, matthews_corrcoef, recall_score

DATA = r"D:\灵境制造\research\datasets\measured_stability\ji2024_digitized_points.csv"
PER_POINT = r"D:\灵境制造\research\experiments\results\step3_error_structure_per_point.csv"

# 论文表 1/2 模态参数
MODAL = {
    ("x", 35): [(565.1, .0322, .1966), (939.1, .0336, .0563), (1311.3, .0375, .0302), (1675.0, .0266, .0249), (2050.3, .0276, .0247)],
    ("x", 65): [(325.5, .0424, .1207), (551.5, .0197, .0941), (743.8, .0315, .0224), (977.6, .0298, .0268), (1184.3, .0268, .1122)],
    ("y", 35): [(538.5, .0730, .0843), (876.6, .0202, .2319), (1283.3, .0260, .0441), (1604.0, .0211, .0422), (1970.0, .0367, .0216)],
    ("y", 65): [(321.3, .0632, .1268), (540.6, .0174, .0919), (732.8, .0235, .0224), (919.3, .0416, .0332), (1164.3, .0273, .0440)],
}


def res_features(n_rpm, feed, overhang):
    """共振接近度 + 主导模态阻尼（无标签泄漏：只用工况与论文模态参数）。"""
    best_dist, best_zeta = np.inf, np.nan
    for (f, zeta, m) in MODAL[(feed, overhang)]:
        for j in range(1, 11):
            f_c = j * n_rpm / 60.0
            d = abs(f_c - f) / f
            if d < best_dist:
                best_dist = d
                best_zeta = zeta
    return best_dist, best_zeta


def load():
    pts = list(csv.DictReader(io.open(PER_POINT, encoding="utf-8")))
    X, meta = [], []
    for r in pts:
        n = float(r["n"]); ap = float(r["ap"])
        oh = int(r["overhang"]); feed = r["feed"]
        a_lim = float(r["a_lim_ae1"])
        margin = (a_lim - ap) / a_lim
        rd, zg = res_features(n, feed, oh)
        X.append([n / 1000.0, ap, oh, 1.0 if feed == "y" else 0.0, margin, rd, zg])
        meta.append(dict(fig=r["fig"], panel=r["panel"], feed=feed, overhang=oh,
                         n=n, ap=ap, actual=r["actual"], pred=r["pred_ae1"]))
    return np.array(X), meta


def loocv_eval(X, y, model, seed=0):
    """LOOCV 预测 + 指标。返回 (bal_acc, mcc, recall_pos, proba)。"""
    loo = LeaveOneOut()
    proba = cross_val_predict(model, X, y, cv=loo, method="predict_proba")[:, 1]
    pred = (proba >= 0.5).astype(int)
    return (balanced_accuracy_score(y, pred), matthews_corrcoef(y, pred),
            recall_score(y, pred, pos_label=1), proba, pred)


def permutation_p(X, y, make_model, observed, n_perm=30, seed=42):
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(n_perm):
        yv = rng.permutation(y)
        m = make_model()
        bal, _, _, _, _ = loocv_eval(X, yv, m)
        null.append(bal)
    null = np.array(null)
    p = (np.sum(null >= observed) + 1) / (n_perm + 1)
    return null.mean(), null.std(), p


def main():
    X, meta = load()
    err = np.array([1.0 if m["pred"] != m["actual"] else 0.0 for m in meta])
    aggr = np.array([1.0 if (m["pred"] == "stable" and m["actual"] == "chatter") else 0.0 for m in meta])
    cons = np.array([1.0 if (m["pred"] == "chatter" and m["actual"] == "stable") else 0.0 for m in meta])
    print("样本 %d | model_error %d | aggressive %d | conservative %d" % (len(X), err.sum(), aggr.sum(), cons.sum()))
    print("特征: n_krpm, ap, overhang, is_y, margin_signed, res_dist, zeta_gov")

    # 无 margin 特征子集（非循环版：不含定义标签所用的量）
    X_nomargin = X[:, [0, 1, 2, 3, 5, 6]]
    NAMES_NM = ["n_krpm", "ap", "overhang", "is_y", "res_dist", "zeta_gov"]
    margin = X[:, 4]

    # 子集：模型说"稳定"（margin>0）——问"该不该信"；特征不含 margin（非循环）
    mask_stable = margin > 0
    # 子集：模型说"颤振"——问"会不会白小心"
    mask_chatter = margin <= 0
    print("模型说稳定: %d 点 | 模型说颤振: %d 点" % (mask_stable.sum(), mask_chatter.sum()))
    print()

    def lr_make():
        return Pipeline([("sc", StandardScaler()),
                         ("clf", LogisticRegression(C=1.0, max_iter=2000))])

    def dt_make():
        return Pipeline([("sc", StandardScaler()),
                         ("clf", DecisionTreeClassifier(max_depth=2, random_state=0))])

    results = {}

    # ---------- T2b（诚实版核心任务）：pred-stable 子集内预测实际颤振 ----------
    print("=" * 78)
    ys = (meta_mask_actual_chatter := None)  # noqa
    Xs = X_nomargin[mask_stable]
    ys_aggr = aggr[mask_stable].astype(int)
    print("T2b 预警器（诚实版）：pred=stable 子集 %d 点（实际颤振 %d），特征不含 margin" %
          (len(Xs), ys_aggr.sum()))
    for tag, mk in [("LR", lr_make), ("Tree(d=2)", dt_make)]:
        bal, mcc, rec, proba, pred = loocv_eval(Xs, ys_aggr, mk())
        print("  %-9s LOOCV: bal_acc=%.3f  MCC=%+.3f  召回=%.3f" % (tag, bal, mcc, rec))
        results["T2b_" + tag] = dict(bal=bal, mcc=mcc, rec=rec)
        if tag == "LR":
            t2b_proba = proba
    mu, sd, p = permutation_p(Xs, ys_aggr, lr_make,
                              results["T2b_LR"]["bal"], n_perm=30)
    print("  置换检验(30次, LR): 零分布 %.3f±%.3f, p=%.4f" % (mu, sd, p))
    results["T2b_perm"] = dict(mu=mu, sd=sd, p=p)

    # 全数据树规则（描述性）
    dt_full = DecisionTreeClassifier(max_depth=2, random_state=0).fit(Xs, ys_aggr)
    print("  全数据树规则（描述性，特征=%s）:" % ",".join(NAMES_NM))
    for ln in export_text(dt_full, feature_names=NAMES_NM).rstrip().splitlines():
        print("    " + ln)

    # margin-only 对照（循环敏感度分析）：单独用 margin 能分多开
    from sklearn.metrics import roc_auc_score
    auc_margin = roc_auc_score(ys_aggr, -margin[mask_stable])  # margin 小 → 更危险
    print("  对照：仅用 margin 排序的 AUC = %.3f（这是'定义性'上限参照）" % auc_margin)

    # ---------- T3b：pred-chatter 子集内预测"白小心"（保守错误） ----------
    print()
    print("=" * 78)
    Xc = X_nomargin[mask_chatter]
    yc_cons = cons[mask_chatter].astype(int)
    print("T3b 预警器：pred=chatter 子集 %d 点（实际稳定 %d），特征不含 margin" %
          (len(Xc), yc_cons.sum()))
    for tag, mk in [("LR", lr_make), ("Tree(d=2)", dt_make)]:
        bal, mcc, rec, proba, pred = loocv_eval(Xc, yc_cons, mk())
        print("  %-9s LOOCV: bal_acc=%.3f  MCC=%+.3f  召回=%.3f" % (tag, bal, mcc, rec))
        results["T3b_" + tag] = dict(bal=bal, mcc=mcc, rec=rec)
    mu, sd, p = permutation_p(Xc, yc_cons, lr_make,
                              results["T3b_LR"]["bal"], n_perm=30)
    print("  置换检验(30次, LR): 零分布 %.3f±%.3f, p=%.4f" % (mu, sd, p))
    results["T3b_perm"] = dict(mu=mu, sd=sd, p=p)

    # ---------- 原三任务（含 margin，作为"margin 校准"参照，标注半循环） ----------
    print()
    print("=" * 78)
    print("参照组（含 margin，属'margin 校准'分析，注意定义性循环）：")
    err_all = err.astype(int)
    for name, y in [("T1_model_error", err_all), ("T2_aggressive", aggr.astype(int)),
                    ("T3_conservative", cons.astype(int))]:
        bal, mcc, rec, proba, pred = loocv_eval(X, y, lr_make())
        bald, mccd, recd, _, _ = loocv_eval(X, y, dt_make())
        print("  %-18s LR: bal=%.3f MCC=%+.3f | Tree(d2): bal=%.3f MCC=%+.3f"
              % (name, bal, mcc, bald, mccd))
        results[name] = dict(pos=int(y.sum()), lr_bal=bal, lr_mcc=mcc,
                             tree_bal=bald, tree_mcc=mccd)

    # margin 分带校准表（物理模型可靠性的直接刻画）
    print()
    print("margin 分带 → 模型判'稳定'的可信度（全数据，描述性）:")
    print("  %-18s %6s %8s %10s" % ("margin 带", "点数", "实际颤振", "稳定精度"))
    bands = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.63), (0.63, 2.0)]
    for lo, hi in bands:
        m = mask_stable & (margin > lo) & (margin <= hi)
        if m.sum() == 0:
            continue
        ch = int(aggr[m].sum())
        print("  (%.2f, %.2f]          %6d %8d %9.1f%%" % (lo, hi, m.sum(), ch, 100 * (1 - ch / m.sum())))

    print()
    import json as _json
    out = {k: v for k, v in results.items()}
    out["_meta"] = dict(n=len(X), features_full=["n_krpm", "ap", "overhang", "is_y",
                                                 "margin_signed", "res_dist", "zeta_gov"],
                        features_nomargin=NAMES_NM)
    io.open(r"D:\灵境制造\research\experiments\results\step4_failure_predictor_results.json", "w", encoding="utf-8").write(
        _json.dumps(out, ensure_ascii=False, indent=2, default=float))
    print("JSON -> step4_failure_predictor_results.json")

    # LOOCV 概率落盘（T2b 逐点）
    with io.open(r"D:\灵境制造\research\experiments\results\step4_loocv_probabilities.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fig", "panel", "feed", "overhang", "n_rpm", "ap", "actual", "pred_M1",
                    "margin", "p_T2b_chatter_given_stable"])
        for i, m in enumerate(meta):
            w.writerow([m["fig"], m["panel"], m["feed"], m["overhang"], m["n"], m["ap"],
                        m["actual"], m["pred"], round(float(margin[i]), 4),
                        round(float(t2b_proba[i]), 4) if mask_stable[i] else ""])
    print("CSV -> step4_loocv_probabilities.csv")


if __name__ == "__main__":
    main()
