"""世界模型轨迹数据集构建器（T1-1，物理一致合成轨迹 v0）.

背景
----
``FusionTrajectoryDataset`` 需要四元组 ``(geometry_seq, dynamics_seq,
actions, target_trajectory)``，但仓库内既没有现成轨迹数据，也没有
真实加工过程的状态标签。本脚本按 ADR-017 风险表既定策略
（"v1 优先用仿真数据预训练，真实数据微调"）生成**物理一致的合成轨迹**：

- 几何：块料包围盒 + 由包围盒/特征计数确定性派生的 32 维几何特征向量
- 动力学：主轴转速 / 进给 / 切深 / 磨损 / 振动 RMS / 温度 的时间序列
- 状态标签（8 维）：
  ``[chatter_probability, tool_wear, surface_roughness, vibration_rms,
     temperature/100, cutting_force_norm, mrr_norm, confidence]``
- 物理内核（全部复用仓库既有模块，不另造物理）：
  * 颤振：``app.simulation.chatter.stability.compute_stability_limit``
    （Tlusty 单自由度解析解）
  * 切削力：``app.simulation.cutting_force.kienzle.compute_cutting_forces``
  * 磨损：Archard 风格增量模型 ``dw = k_w * F * v * dt``（经验系数，
    文档见 docs/reports/wm_state_schema_20261021.md）
  * 表面粗糙度：``Ra = 0.5 + 60*fz^1.5 + 2.5*chatter + 3*wear``（µm，经验式）

**来源标注**：产出的 meta 里 ``provenance = "physics_synthetic_v0"``。
这些不是实测数据，不得对外宣称"真实加工数据训练"。

用法（工作目录 ``engineering/python``）::

    python scripts/build_wm_trajectory_dataset.py \
        --samples 1500 --history 20 --horizon 5 \
        --out data/world_model/trajectories_v1.npz

退出码
------
0 — 数据集生成并通过 ``validate_sample`` 全量校验
1 — 生成失败 / 校验不通过
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

import numpy as np  # noqa: E402

from app.plugins.world_model.training.fusion_dataset import (  # noqa: E402
    FusionSampleError,
    validate_sample,
)
from app.simulation.chatter.stability import (  # noqa: E402
    MachineParams,
    get_machine_params,
)
from app.simulation.cutting_force.kienzle import compute_cutting_forces  # noqa: E402

GEO_DIM = 37
DYN_DIM = 6
ACTION_DIM = 4
STATE_DIM = 8
FEATURE_DIM = 32

PROVENANCE = "physics_synthetic_v0"

MATERIALS = ["45steel", "aluminum_6061", "stainless_304", "cast_iron_ht200", "titanium_tc4"]
TOOLS = [
    ("endmill_d10", 10.0, 4, 2000.0),
    ("endmill_d16", 16.0, 4, 2200.0),
    ("endmill_d20", 20.0, 5, 2400.0),
]

# 稳定性极限缓存：按 (tool_id, machine_id) 缓存
_A_LIM_CACHE: dict[tuple[str, str], float] = {}


def tlusty_a_lim(stiffness_n_per_m: float, damping_ratio: float, ks_n_per_mm2: float) -> float:
    """单自由度 Tlusty 稳定性极限切深（闭式解，单位 mm）.

    理论：``a_lim = 1 / (2 · Ks · |min Re[G]|)``，一自由度 FRF
    ``G(ω) = 1/(k - mω² + i·cω)``，其实部最负值近似为
    ``|Re[G]|max = 1 / (4·k·ζ·(1+ζ))``（k 取 N/mm）。

    为什么不直接用 ``compute_stability_limit``
    ------------------------------------------
    仓库该实现（``app/simulation/chatter/stability.py``）在 vmc_850 上返回
    约 30.6 mm、d20 刀具约 25.5 mm，量级偏离真实钢件铣削（通常 0.5–3 mm），
    且不随转速变化（未使用 spindle_rpm）。用它生成标签会让颤振概率恒定
    接近 0。本脚本按其文档公式重算，**不改动公共模块**（避免影响 ADR-013
    既有链路与测试）；差异与限制记录在
    ``docs/reports/wm_state_schema_20261021.md`` §3。

    Returns
    -------
    float
        极限切深 mm，裁剪到 [0.05, 20]。
    """
    k_mm = stiffness_n_per_m / 1000.0  # N/m -> N/mm
    re_g_min = 1.0 / (4.0 * k_mm * damping_ratio * (1.0 + damping_ratio))
    a_lim = 1.0 / (2.0 * ks_n_per_mm2 * re_g_min)
    return float(np.clip(a_lim, 0.05, 20.0))


def a_lim_for(tool_id: str, ks: float, machine: MachineParams) -> float:
    """按（刀具, 机床）缓存的极限切深.

    v0 限制：1-DOF 包络解与转速无关（不含叶瓣结构），转速只通过
    力/磨损/温度间接影响状态。真实叶瓣结构待 ADR-013 实测标定后替换。
    """
    key = (tool_id, machine.machine_id)
    cached = _A_LIM_CACHE.get(key)
    if cached is not None:
        return cached
    value = tlusty_a_lim(machine.stiffness_x, machine.damping_ratio, ks)
    _A_LIM_CACHE[key] = value
    return value


def synth_geometry(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, float, float]:
    """生成 (几何序列[37] 每步相同, 用于 T 步堆叠) 的原始向量.

    Returns
    -------
    tuple
        (geo_vector[37], bbox[3], symmetry, complexity)
    """
    bbox = np.array(
        [
            rng.uniform(60.0, 200.0),   # length mm
            rng.uniform(40.0, 150.0),   # width mm
            rng.uniform(20.0, 80.0),    # height mm
        ],
        dtype=np.float32,
    )
    # 32 维特征向量：ADR-007 布局的合成近似（文档化，非实测）
    # [0:8]   平面面积占比（上下/四侧/两个台阶面），按 bbox 归一化
    # [8:16]  圆柱特征（直径/高/数量/覆盖率）
    # [16:24] 孔特征（数量/直径/深径比/分布密度）
    # [24:32] 槽/凸台特征（数量/宽度/深度/间距）
    l, w, h = float(bbox[0]), float(bbox[1]), float(bbox[2])
    area = l * w
    planes = np.array(
        [
            area / (2 * (l * w + l * h + w * h)),
            area / (2 * (l * w + l * h + w * h)),
            l * h / (2 * (l * w + l * h + w * h)),
            l * h / (2 * (l * w + l * h + w * h)),
            w * h / (2 * (l * w + l * h + w * h)),
            w * h / (2 * (l * w + l * h + w * h)),
            rng.uniform(0.0, 0.2),
            rng.uniform(0.0, 0.2),
        ],
        dtype=np.float32,
    )
    n_cyl = int(rng.integers(0, 8))
    cyl = np.array(
        [
            n_cyl / 8.0,
            rng.uniform(0.1, 1.0),
            rng.uniform(0.1, 1.0),
            rng.uniform(0.0, 0.6),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
        ],
        dtype=np.float32,
    )
    n_holes = int(rng.integers(0, 24))
    holes = np.array(
        [
            n_holes / 24.0,
            rng.uniform(0.05, 1.0),
            rng.uniform(0.2, 3.0) / 3.0,
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
        ],
        dtype=np.float32,
    )
    n_slots = int(rng.integers(0, 10))
    slots = np.array(
        [
            n_slots / 10.0,
            rng.uniform(0.05, 0.8),
            rng.uniform(0.05, 0.8),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
            rng.uniform(0.0, 1.0),
        ],
        dtype=np.float32,
    )
    feature_vector = np.concatenate([planes, cyl, holes, slots]).astype(np.float32)
    assert feature_vector.shape[0] == FEATURE_DIM

    symmetry = float(np.clip(1.0 - (n_holes + n_slots) / 40.0, 0.0, 1.0))
    complexity = float(np.clip((n_cyl * 2 + n_holes + n_slots * 3) / 60.0, 0.0, 1.0))
    geo_vector = np.concatenate([bbox, feature_vector, [symmetry, complexity]]).astype(np.float32)
    assert geo_vector.shape[0] == GEO_DIM
    return geo_vector, bbox, symmetry, complexity


def simulate_episode(
    rng: np.random.Generator,
    steps: int,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """模拟一条加工轨迹.

    Returns
    -------
    tuple
        (geometry_seq[T,37], dynamics_seq[T,6], actions[T+H,4],
         target[H,8])
    """
    machine_id = str(rng.choice(["vmc_850", "small_vmc_640"]))
    # 用预设机床参数（get_machine_params 会填入刚度/阻尼/固有频率），
    # 直接 MachineParams(machine_id=...) 不会加载预设，导致机床间无差异
    machine = get_machine_params(machine_id)
    tool_id, diameter, flutes, ks = TOOLS[int(rng.integers(0, len(TOOLS)))]
    material = str(rng.choice(MATERIALS))

    rpm = float(rng.uniform(2000.0, 12000.0))
    feed = float(rng.uniform(200.0, 2000.0))
    # 切深相对稳定性极限采样（0.2x ~ 1.6x）：保证颤振概率覆盖
    # 稳定区 / 临界区 / 不稳定区三段，否则样本几乎全是 p_chatter≈0，
    # 世界模型学不到颤振相关信号。
    a_lim0 = a_lim_for(tool_id, ks, machine)
    depth = float(np.clip(a_lim0 * rng.uniform(0.2, 1.6), 0.05, 10.0))
    wear = float(rng.uniform(0.0, 0.15))

    geo_vector, bbox, _sym, _cplx = synth_geometry(rng)
    geometry_seq = np.tile(geo_vector, (steps, 1)).astype(np.float32)
    dynamics_seq = np.zeros((steps, DYN_DIM), dtype=np.float32)
    actions = np.zeros((steps + horizon, ACTION_DIM), dtype=np.float32)

    # 未来动作：小幅控制扰动（模拟操作员/自适应策略），历史动作置零
    for t in range(steps, steps + horizon):
        actions[t] = np.array(
            [
                rng.normal(0.0, 200.0),
                rng.normal(0.0, 50.0),
                rng.normal(0.0, 0.05),
                1.0 if rng.random() < 0.5 else 0.0,
            ],
            dtype=np.float32,
        )

    fz = max(feed / max(rpm * flutes, 1e-6), 0.0)  # 每齿进给 mm/tooth
    temp = 25.0
    states = np.zeros((steps + horizon, STATE_DIM), dtype=np.float32)
    dts = 1.0  # 每步 1 个采样周期（无量纲仿真步）

    for t in range(steps + horizon):
        a_lim = a_lim_for(tool_id, ks, machine)
        # 颤振概率：切深相对极限的 sigmoid 裕度
        margin = (depth - a_lim) / max(0.15 * a_lim, 1e-6)
        chatter_p = float(1.0 / (1.0 + np.exp(-margin)))

        forces = compute_cutting_forces(
            material=material,
            width=max(depth, 1e-3),
            chip_thickness=max(fz, 1e-4),
        )
        fz_force = float(forces.get("Fz", 0.0))

        # 磨损：Archard 风格增量（经验系数 k_w = 2e-9 mm^3/(N*mm)）
        wear = float(min(wear + 2.0e-9 * fz_force * max(rpm, 1.0) * dts, 0.6))
        # 表面粗糙度（µm，经验式）
        ra = float(0.5 + 60.0 * (fz**1.5) + 2.5 * chatter_p + 3.0 * wear)
        # 振动 RMS（g）：切削力 + 颤振放大 + 噪声
        vib = float(np.clip(0.0008 * fz_force + 1.5 * chatter_p + rng.normal(0.0, 0.05), 0.0, 10.0))
        # 温度（°C）：切削功率引起的温升 + 热惯性一阶滤波
        target_temp = 25.0 + 0.004 * fz_force * (rpm / 1000.0)
        temp = float(temp + 0.35 * (target_temp - temp))
        # 材料去除率（cm^3/min）
        ae = 0.5 * diameter
        mrr = float(depth * ae * feed / 1000.0)
        confidence = float(np.clip(1.0 - 0.5 * chatter_p, 0.0, 1.0))

        states[t] = np.array(
            [
                chatter_p,
                wear,
                ra,
                vib,
                temp / 100.0,
                fz_force / 1000.0,
                mrr / 100.0,
                confidence,
            ],
            dtype=np.float32,
        )
        if t < steps:
            dynamics_seq[t] = np.array([rpm, feed, depth, wear, vib, temp], dtype=np.float32)
        # 轻微漂移，让轨迹不是常数
        rpm = float(np.clip(rpm + rng.normal(0.0, 30.0), 1500.0, 15000.0))
        feed = float(np.clip(feed + rng.normal(0.0, 8.0), 100.0, 3000.0))
        depth = float(np.clip(depth + rng.normal(0.0, 0.02), 0.05, 4.0))

    target = states[steps : steps + horizon].copy()
    # ADR-023：最后观测状态（状态反馈训练的 initial_state）
    initial_state = states[steps - 1].copy()
    return geometry_seq, dynamics_seq, actions, target, initial_state


def main() -> int:
    parser = argparse.ArgumentParser(description="构建世界模型轨迹数据集（物理一致合成 v0）")
    parser.add_argument("--samples", type=int, default=1500)
    parser.add_argument("--history", type=int, default=20)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default="data/world_model/trajectories_v1.npz")
    args = parser.parse_args()

    if args.samples <= 0 or args.history <= 0 or args.horizon <= 0:
        print("[dataset] samples/history/horizon 必须为正整数", file=sys.stderr)
        return 1

    rng = np.random.default_rng(args.seed)
    geo_all, dyn_all, act_all, tgt_all, init_all = [], [], [], [], []

    for i in range(args.samples):
        geo, dyn, act, tgt, init_state = simulate_episode(rng, args.history, args.horizon)
        # 用项目自带校验器严格校验（形状/有限性/时间维一致性）
        try:
            validate_sample(
                geo,
                dyn,
                act,
                tgt,
                geo_input_dim=GEO_DIM,
                dynamics_input_dim=DYN_DIM,
                action_dim=ACTION_DIM,
                state_dim=STATE_DIM,
            )
        except FusionSampleError as exc:
            print(f"[dataset] 样本 {i} 校验失败: {exc}", file=sys.stderr)
            return 1
        geo_all.append(geo)
        dyn_all.append(dyn)
        act_all.append(act)
        tgt_all.append(tgt)
        init_all.append(init_state)

    geo_arr = np.stack(geo_all).astype(np.float32)
    dyn_arr = np.stack(dyn_all).astype(np.float32)
    act_arr = np.stack(act_all).astype(np.float32)
    tgt_arr = np.stack(tgt_all).astype(np.float32)
    init_arr = np.stack(init_all).astype(np.float32)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _PYTHON_ROOT.parent.parent / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        out_path,
        geometry=geo_arr,
        dynamics=dyn_arr,
        actions=act_arr,
        target=tgt_arr,
        initial_state=init_arr,
    )

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": PROVENANCE,
        "note": "物理一致合成轨迹（Tlusty 稳定性 + Kienzle 力 + 经验磨损/粗糙度），非实测数据",
        "samples": int(geo_arr.shape[0]),
        "history": int(geo_arr.shape[1]),
        "horizon": int(tgt_arr.shape[1]),
        "dims": {
            "geometry": GEO_DIM,
            "dynamics": DYN_DIM,
            "action": ACTION_DIM,
            "state": STATE_DIM,
        },
        "state_semantics": [
            "chatter_probability",
            "tool_wear_mm",
            "surface_roughness_um",
            "vibration_rms_g",
            "temperature_over_100c",
            "cutting_force_norm",
            "mrr_norm",
            "confidence",
        ],
        "seed": args.seed,
        "a_lim_cache_entries": len(_A_LIM_CACHE),
        "shapes": {
            "geometry": list(geo_arr.shape),
            "dynamics": list(dyn_arr.shape),
            "actions": list(act_arr.shape),
            "target": list(tgt_arr.shape),
        },
        "target_range": {
            "min": [round(float(v), 6) for v in tgt_arr.min(axis=(0, 1))],
            "max": [round(float(v), 6) for v in tgt_arr.max(axis=(0, 1))],
            "mean": [round(float(v), 6) for v in tgt_arr.mean(axis=(0, 1))],
        },
    }
    meta_path = out_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[dataset] 完成: samples={geo_arr.shape[0]} T={geo_arr.shape[1]} H={tgt_arr.shape[1]} "
        f"out={out_path}",
        file=sys.stderr,
    )
    print(f"[dataset] meta={meta_path} provenance={PROVENANCE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
