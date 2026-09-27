"""曲面案例族定义（machinability pilot，见 docs/paper_and_competition/F2）。

每个设计族提供 5 个难度档的参数化 CadQuery 构建，产出带已知曲面特征的
真值模型，作为 LLM 生成结果的评估基准。真值代码是本仓库自己编写的可信
代码，直接进程内构建；沙箱（app/cad/_cadquery_helpers）留给 S1 阶段执行
不可信的 LLM 输出。

所有模型单位为 mm；几何保持保守（圆角半径 < 壁厚一半、截面连续过渡），
确保真值本身可被 OCCT 稳定构建。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Callable

import cadquery as cq

DIFFICULTY_TIERS = ("d1", "d2", "d3", "d4", "d5")


@dataclass(frozen=True)
class CaseFamily:
    """一个参数化设计族：难度档 → 参数 → 实体 → 英文几何描述。"""

    family_id: str
    name: str
    surface_features: tuple[str, ...]
    params_for: Callable[[str], dict]
    build: Callable[[dict], cq.Workplane]
    describe: Callable[[dict], str]


# ---------------------------------------------------------------- F01 过渡接头


def _f01_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "height": 48.0 - 8.0 * k,
        "r_bottom": 18.0 + k,
        "a_mid": 24.0 - 2.0 * k,
        "b_mid": 10.0 + 2.0 * k,
        "w_top": 26.0 - 3.0 * k,
        "l_top": 26.0 + 3.0 * k,
        "ruled": k >= 3,
    }


def _f01_build(p: dict) -> cq.Workplane:
    h = p["height"]
    return (
        cq.Workplane("XY")
        .circle(p["r_bottom"])
        .workplane(offset=h / 2)
        .ellipse(p["a_mid"], p["b_mid"])
        .workplane(offset=h / 2)
        .rect(p["w_top"], p["l_top"])
        .loft(ruled=p["ruled"])
    )


def _f01_desc(p: dict) -> str:
    ruled = "ruled (straight) transitions" if p["ruled"] else "smooth transitions"
    return (
        f"A transition fitting that lofts from a circular base of radius {p['r_bottom']:.0f} mm "
        f"through an elliptical mid-section {p['a_mid']:.0f} x {p['b_mid']:.0f} mm into a "
        f"rectangular top opening {p['w_top']:.0f} x {p['l_top']:.0f} mm, over a height of "
        f"{p['height']:.0f} mm with {ruled}."
    )


# ---------------------------------------------------------------- F02 螺旋输送


def _f02_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "radius": 16.0,
        "pitch": 22.0 - 2.5 * k,
        "height": 44.0 + 6.0 * k,
        "core_r": 8.0,
        "web_w": 20.0,
        "web_t": 5.0 - 0.6 * k,
    }


def _f02_build(p: dict) -> cq.Workplane:
    helix = cq.Wire.makeHelix(pitch=p["pitch"], height=p["height"], radius=p["radius"])
    flight = cq.Workplane("XZ", origin=(p["radius"], 0, 0)).rect(p["web_w"], p["web_t"]).sweep(helix, isFrenet=True)
    core = cq.Workplane("XY").circle(p["core_r"]).extrude(p["height"])
    return core.union(flight)


def _f02_desc(p: dict) -> str:
    turns = p["height"] / p["pitch"]
    return (
        f"A vertical conveyor screw with a solid cylindrical core of radius {p['core_r']:.0f} mm "
        f"and {turns:.1f} helical flights swept along a helix of radius {p['radius']:.0f} mm, "
        f"each flight {p['web_w']:.0f} mm wide and {p['web_t']:.1f} mm thick with a pitch of "
        f"{p['pitch']:.1f} mm over a total height of {p['height']:.0f} mm."
    )


# ---------------------------------------------------------------- F03 波形模具


def _f03_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "length": 70.0,
        "width": 34.0,
        "base_h": 10.0,
        "amplitude": 2.0 + 1.5 * k,
        "cycles": 2 + k,
        "samples": 160,
    }


def _f03_build(p: dict) -> cq.Workplane:
    half = p["length"] / 2
    xs = [-half + p["length"] * i / (p["samples"] - 1) for i in range(p["samples"])]
    # 波浪整体抬在 base_h 基座上：若波谷落在底面以下，闭合轮廓会自相交
    # （正弦过零点与底边相交），OCCT 只会给出零体积实体
    top_pts = [
        (x, p["base_h"] + p["amplitude"] * math.sin(2 * math.pi * p["cycles"] * (x + half) / p["length"])) for x in xs
    ]
    profile = top_pts + [(half, 0.0), (-half, 0.0)]
    return cq.Workplane("XZ").polyline(profile).close().extrude(p["width"])


def _f03_desc(p: dict) -> str:
    return (
        f"A rectangular mold plate {p['length']:.0f} x {p['width']:.0f} mm whose top face is a "
        f"sinusoidal wave surface with {p['cycles']} full waves and amplitude {p['amplitude']:.1f} mm "
        f"raised on a {p['base_h']:.0f} mm base, with a flat bottom."
    )


# ---------------------------------------------------------------- F04 凸轮盘


def _f04_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "r_base": 22.0,
        "amplitude": 3.0 + 1.5 * k,
        "lobes": 3 + k,
        "thickness": 12.0 - 1.5 * k,
        "bore_d": 8.0,
        "samples": 144,
    }


def _f04_build(p: dict) -> cq.Workplane:
    pts = []
    for i in range(p["samples"]):
        t = 2 * math.pi * i / p["samples"]
        r = p["r_base"] + p["amplitude"] * math.sin(p["lobes"] * t)
        pts.append((r * math.cos(t), r * math.sin(t)))
    # 144 段 polyline：r≈25mm 时弦差 <0.01mm，几何上等价光滑轮廓且构建稳健
    return cq.Workplane("XY").polyline(pts).close().extrude(p["thickness"]).faces(">Z").workplane().hole(p["bore_d"])


def _f04_desc(p: dict) -> str:
    return (
        f"A flat cam disk of thickness {p['thickness']:.1f} mm whose outline is a smooth "
        f"multi-lobe curve with {p['lobes']} sinusoidal lobes of amplitude {p['amplitude']:.1f} mm "
        f"on a base radius of {p['r_base']:.0f} mm, with a central through bore of "
        f" diameter {p['bore_d']:.0f} mm."
    )


# ---------------------------------------------------------------- F05 弯管流道


def _f05_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "bend_r": 34.0 - 4.0 * k,
        "duct_r": 9.0 + 0.8 * k,
        "flange_t": 6.0,
        "bolt_n": 2 * k,
    }


def _f05_build(p: dict) -> cq.Workplane:
    r = p["bend_r"]
    dr = p["duct_r"]
    # U 形弯用旋转成型（MakeRevol）而非路径扫掠：半圆路径扫掠在弯径/管径比
    # 恶化时会退化为零体积/反体积实体，旋转成型全程稳定
    duct = cq.Workplane("XZ", origin=(r, 0, 0)).circle(dr).revolve(180, (-r, 0), (-r, 1))
    flange_in = cq.Workplane("XZ", origin=(r, 0, 0)).circle(dr + 3.0).extrude(p["flange_t"])
    flange_out = cq.Workplane("XZ", origin=(-r, 0, 0)).circle(dr + 3.0).extrude(-p["flange_t"])
    part = duct.union(flange_in).union(flange_out)
    if p["bolt_n"] > 0:
        bolt_circle_r = dr + 1.5
        for cx, sign in ((r, 1), (-r, -1)):  # 分别穿透 -Y / +Y 侧法兰
            pts = [
                (
                    bolt_circle_r * math.cos(2 * math.pi * i / p["bolt_n"]),
                    bolt_circle_r * math.sin(2 * math.pi * i / p["bolt_n"]),
                )
                for i in range(p["bolt_n"])
            ]
            holes = (
                cq.Workplane("XZ", origin=(cx, 0, 0)).pushPoints(pts).circle(1.5).extrude(sign * (p["flange_t"] + 1.0))
            )
            part = part.cut(holes)
    return part


def _f05_desc(p: dict) -> str:
    bolts = (
        f", each flange carrying {p['bolt_n']} bolt holes of diameter 3 mm on a bolt circle" if p["bolt_n"] > 0 else ""
    )
    return (
        f"A 180-degree U-bend duct of circular cross-section radius {p['duct_r']:.1f} mm and "
        f"bend radius {p['bend_r']:.0f} mm, with circular mounting flanges {p['flange_t']:.0f} mm "
        f"thick on both open ends{bolts}."
    )


# ---------------------------------------------------------------- F06 叶片支架


def _airfoil_loop(chord: float, thick: float, camber: float, n: int = 20) -> list[tuple[float, float]]:
    """透镜状翼型截面环：上表面左→右 + 下表面右→左。"""
    upper, lower = [], []
    for i in range(n):
        x = chord * i / (n - 1)
        env = math.sin(math.pi * x / chord)
        yc = camber * env
        t = 0.5 * thick * env
        upper.append((x, yc + t))
        lower.append((x, yc - t))
    # 两端各去掉一个采样点：x=chord 处 sin(π)≈1e-16，若两个表面都保留该点
    # 会产生 1e-16 间距的近零长边，OCCT 构建失败
    return upper + lower[::-1][1:-1]


def _f06_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "base_l": 70.0,
        "base_w": 40.0,
        "base_t": 8.0,
        "chord": 30.0,
        "thick": 8.0 - 1.0 * k,
        "camber": 3.0 + 0.8 * k,
        "blade_h": 24.0 + 3.0 * k,
        "tip_scale": 0.9 - 0.08 * k,
        "blade_x": -12.0,
    }


def _f06_build(p: dict) -> cq.Workplane:
    base = cq.Workplane("XY").box(p["base_l"], p["base_w"], p["base_t"], centered=(True, True, False))
    pts1 = _airfoil_loop(p["chord"], p["thick"], p["camber"])
    pts2 = [(x * p["tip_scale"], y * p["tip_scale"]) for x, y in pts1]
    blade = (
        cq.Workplane("XY", origin=(p["blade_x"], 0, p["base_t"]))
        .polyline(pts1)
        .close()
        .workplane(offset=p["blade_h"])
        .polyline(pts2)
        .close()
        .loft()
    )
    return base.union(blade)


def _f06_desc(p: dict) -> str:
    return (
        f"A mounting bracket with a rectangular base plate {p['base_l']:.0f} x {p['base_w']:.0f} x "
        f"{p['base_t']:.0f} mm carrying a single vertical blade: a lofted airfoil section "
        f"{p['chord']:.0f} mm chord with camber {p['camber']:.1f} mm and thickness "
        f"{p['thick']:.1f} mm, tapering to {p['tip_scale'] * 100:.0f}% of its section at the top, "
        f"rising {p['blade_h']:.0f} mm above the base."
    )


# ---------------------------------------------------------------- F07 圆角槽块


def _f07_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    h = 18.0 - 2.0 * k
    slot_w = 8.0 - 0.8 * k
    n_slots = 1 + k
    slots = [
        {
            "x": -18.0 + 9.0 * i,
            "y": 0.0,
            "sl": 14.0 - k,
            "sw": slot_w,
            "depth": min(6.0 + k, h - 4.0),  # 保持盲袋，不铣穿底板
            "fr": round(min(1.0 + 0.4 * k, slot_w * 0.3), 2),
        }
        for i in range(n_slots)
    ]
    return {
        "l": 60.0,
        "w": 40.0,
        "h": h,
        "r_v": 3.0 + 0.5 * k,
        "slots": slots,
        "top_chamfer": min(1.0 + 0.5 * k, 1.5) if k >= 2 else 0.0,
    }


def _f07_build(p: dict) -> cq.Workplane:
    body = cq.Workplane("XY").box(p["l"], p["w"], p["h"], centered=(True, True, False)).edges("|Z").fillet(p["r_v"])
    # 倒角先于切槽：只处理外边界，避免与槽口边缘的布尔交互导致 OCCT 失败
    if p["top_chamfer"] > 0:
        body = body.faces(">Z").chamfer(p["top_chamfer"])
    for s in p["slots"]:
        cutter = (
            cq.Workplane("XY", origin=(s["x"], s["y"], p["h"]))
            .rect(s["sl"], s["sw"])
            .extrude(-s["depth"])
            .edges("|Z")
            .fillet(s["fr"])
        )
        body = body.cut(cutter)
    return body


def _f07_desc(p: dict) -> str:
    s0 = p["slots"][0]
    return (
        f"A rectangular block {p['l']:.0f} x {p['w']:.0f} x {p['h']:.0f} mm with all four vertical "
        f"corners filleted at radius {p['r_v']:.1f} mm, containing {len(p['slots'])} vertical "
        f"slot(s) about {s0['sl']:.0f} x {s0['sw']:.1f} mm and {s0['depth']:.0f} mm deep with "
        f"filleted internal corners of radius {s0['fr']:.2f} mm"
        + (f", and a {p['top_chamfer']:.1f} mm chamfer around the top face." if p["top_chamfer"] else ".")
    )


# ---------------------------------------------------------------- F08 扭转棱柱


def _f08_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "w": 26.0 - 2.0 * k,
        "l": 26.0 - 2.0 * k,
        "h": 40.0 + 8.0 * k,
        "angle": 90.0 + 60.0 * k,
        "bore_d": 6.0 + k,
    }


def _f08_build(p: dict) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .rect(p["w"], p["l"])
        .twistExtrude(p["h"], p["angle"])
        .faces(">Z")
        .workplane()
        .hole(p["bore_d"])
    )


def _f08_desc(p: dict) -> str:
    return (
        f"A square prism {p['w']:.0f} x {p['l']:.0f} mm twisted by {p['angle']:.0f} degrees over "
        f"its height of {p['h']:.0f} mm, creating a helical ruled surface on all four sides, with a central vertical through bore of diameter "
        f"{p['bore_d']:.0f} mm."
    )


# ---------------------------------------------------------------- F09 收敛腔板


def _f09_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "l": 64.0,
        "w": 44.0,
        "h": 16.0,
        "cl1": 40.0 - 2.0 * k,
        "cw1": 26.0,
        "cl2": 26.0 + k,
        "cw2": 14.0 + 2.0 * k,
        "cavity_d": 10.0 + 1.2 * k,
        "hole_d": 4.0,
        "nx": 2 + (k + 1) // 2,
        "ny": 2,
        "spacing": 12.0,
    }


def _f09_build(p: dict) -> cq.Workplane:
    base = cq.Workplane("XY").box(p["l"], p["w"], p["h"], centered=(True, True, False))
    cavity = (
        cq.Workplane("XY", origin=(0, 0, p["h"] + 2.0))
        .rect(p["cl1"], p["cw1"])
        .workplane(offset=-(p["cavity_d"] + 2.0))
        .rect(p["cl2"], p["cw2"])
        .loft()
    )
    part = base.cut(cavity)
    return part.faces(">Z").workplane().rarray(p["spacing"], p["spacing"], p["nx"], p["ny"]).hole(p["hole_d"])


def _f09_desc(p: dict) -> str:
    return (
        f"A rectangular plate {p['l']:.0f} x {p['w']:.0f} x {p['h']:.0f} mm with a centrally "
        f"lofted pocket that converges from {p['cl1']:.0f} x {p['cw1']:.0f} mm at the top to "
        f"{p['cl2']:.0f} x {p['cw2']:.0f} mm at a depth of {p['cavity_d']:.1f} mm, and a grid of "
        f"{p['nx']} x {p['ny']} through holes of diameter {p['hole_d']:.0f} mm drilled through "
        "the surrounding top face."
    )


# ---------------------------------------------------------------- F10 组合件


def _f10_params(d: str) -> dict:
    k = DIFFICULTY_TIERS.index(d)
    return {
        "base_l": 56.0,
        "base_w": 36.0,
        "base_t": 8.0,
        "boss_w": 18.0 - 1.5 * k,
        "boss_h": 22.0 + 3.0 * k,
        "twist": 60.0 + 45.0 * k,
        "fillet_r": 3.0,
        "hole_d": 5.0,
    }


def _f10_build(p: dict) -> cq.Workplane:
    base = (
        cq.Workplane("XY")
        .box(p["base_l"], p["base_w"], p["base_t"], centered=(True, True, False))
        .edges("|Z")
        .fillet(p["fillet_r"])
        .faces(">Z")
        .workplane()
        .pushPoints([(-20.0, -10.0), (20.0, -10.0), (-20.0, 10.0), (20.0, 10.0)])
        .hole(p["hole_d"])
    )
    boss = (
        cq.Workplane("XY", origin=(0, 0, p["base_t"]))
        .rect(p["boss_w"], p["boss_w"])
        .twistExtrude(p["boss_h"], p["twist"])
    )
    return base.union(boss)


def _f10_desc(p: dict) -> str:
    return (
        f"A base plate {p['base_l']:.0f} x {p['base_w']:.0f} x {p['base_t']:.0f} mm with corner "
        f"fillets of radius {p['fillet_r']:.0f} mm and four corner through holes of diameter "
        f"{p['hole_d']:.0f} mm, carrying a central square boss {p['boss_w']:.1f} mm across that "
        f"is twisted by {p['twist']:.0f} degrees over its height of {p['boss_h']:.0f} mm."
    )


FAMILY_REGISTRY: dict[str, CaseFamily] = {
    f.family_id: f
    for f in (
        CaseFamily("F01", "过渡接头", ("loft", "mixed-section transition"), _f01_params, _f01_build, _f01_desc),
        CaseFamily("F02", "螺旋输送", ("helical sweep", "thread surface"), _f02_params, _f02_build, _f02_desc),
        CaseFamily("F03", "波形模具", ("spline freeform wave",), _f03_params, _f03_build, _f03_desc),
        CaseFamily("F04", "凸轮盘", ("periodic spline cam profile",), _f04_params, _f04_build, _f04_desc),
        CaseFamily("F05", "弯管流道", ("arc sweep", "S-duct"), _f05_params, _f05_build, _f05_desc),
        CaseFamily("F06", "叶片支架", ("airfoil loft", "tapered blade"), _f06_params, _f06_build, _f06_desc),
        CaseFamily("F07", "圆角槽块", ("fillet chain", "slot corners"), _f07_params, _f07_build, _f07_desc),
        CaseFamily("F08", "扭转棱柱", ("twisted ruled surface",), _f08_params, _f08_build, _f08_desc),
        CaseFamily("F09", "收敛腔板", ("converging loft pocket",), _f09_params, _f09_build, _f09_desc),
        CaseFamily("F10", "组合件", ("twist extrude", "fillet", "hole pattern"), _f10_params, _f10_build, _f10_desc),
    )
}


def all_case_ids() -> list[str]:
    """全部 50 个案例 ID，顺序稳定（族 × 难度）。"""
    return [f"{fid}-{d}" for fid in FAMILY_REGISTRY for d in DIFFICULTY_TIERS]
