"""方法三件套的接地层：API 卡片索引 + 惯用法技能库 + 分类错误反馈。

研究背景见 docs/paper_and_competition/F0 v2（曲面 CAD 代码生成方法，科研端主方向）。
三个组件分别针对 F3/F5 实测的三类失败模式：
- API 幻觉（编造不存在的 API/参数）        → API 卡片检索接地
- 惯用法缺失（Nothing to loft 等）          → 惯用法技能库
- 失败后盲改（反馈了报错仍然错）            → 分类错误反馈（按错误类型注入修复知识）

v1 口径（免训练）：检索为确定性规则（意图关键词 + 错误模式匹配），嵌入检索
留作升级项。API 卡片由 build_api_cards() 程序化抽取；惯用法卡片人工编写
（idiom_cards.py），是从真值代码调试教训中蒸馏的通用配方，不含任何真值
案例原文（留出守护：技能库增益不构成数据泄漏）。
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import cadquery as cq

DATA_DIR = Path(__file__).resolve().parent / "data"
API_CARDS_PATH = DATA_DIR / "api_cards.json"

# 提示词中始终携带的基础 API（最常用核心链）
BASE_API_NAMES = [
    "Workplane.box",
    "Workplane.circle",
    "Workplane.extrude",
    "Workplane.workplane",
    "Workplane.union",
    "Workplane.cut",
    "Workplane.fillet",
    "Workplane.chamfer",
    "Workplane.hole",
    "Workplane.faces",
    "Workplane.edges",
    "exporters.export",
]

# 意图规则：描述文本匹配 → 注入的 API 卡 + 惯用法卡
INTENT_RULES: list[tuple[str, list[str], list[str]]] = [
    (r"helix|helical|spiral|螺旋", ["Wire.makeHelix", "Workplane.sweep"], ["helical_sweep"]),
    (r"loft|transition|morph|过渡|放样", ["Workplane.loft", "Workplane.workplane"], ["loft_multi_section"]),
    (r"twist|扭转", ["Workplane.twistExtrude"], ["twisted_prism"]),
    (r"sweep|duct|pipe|弯管|扫掠", ["Workplane.sweep", "Workplane.revolve"], ["sweep_profile", "ubend_revolve"]),
    (r"wave|sinusoid|sin|波|cam|凸轮|airfoil|翼型", ["Workplane.polyline", "Workplane.spline"], ["sampled_profile"]),
    (r"revolve|旋转体|盘", ["Workplane.revolve"], ["revolve_profile"]),
    (r"slot|pocket| cavity|槽|腔", ["Workplane.rect", "Workplane.cutBlind"], ["fillet_chain"]),
    (r"hole|bore|孔", ["Workplane.hole", "Workplane.rarray"], ["hole_array"]),
]

# 常用非 Workplane API 与易混淆对（手工增补，抽取器合并写卡）
EXTRA_API_CARDS: list[dict] = [
    {
        "name": "Wire.makeHelix",
        "signature": "cq.Wire.makeHelix(pitch: float, height: float, radius: float, center=Vector(0,0,0), dir=Vector(0,0,1), angle=360.0, lefthand=False) -> Wire",
        "doc": "Create a helical wire. Sweep a profile along it with Workplane.sweep(path, isFrenet=True). The profile must lie on a plane whose normal matches the helix start tangent (+X at angle 0), e.g. cq.Workplane('XZ', origin=(radius, 0, 0)).",
    },
    {
        "name": "exporters.export",
        "signature": "cq.exporters.export(w: Workplane | Shape, fname: str, exportType=None, tolerance=0.1, angularTolerance=0.1) -> None",
        "doc": "Export to STEP/STL/OBJ/GLTF; format inferred from file suffix when exportType is None.",
    },
    {
        "name": "importers.importStep",
        "signature": "cq.importers.importStep(fname: str) -> Workplane",
        "doc": "Import a STEP file as a Workplane.",
    },
]

# 混淆对：模型最常写错的 API 形态（写卡时在 doc 里显式纠偏）
CONFUSION_NOTES = {
    "Workplane.loft": "loft() has NO `sections`/`profiles` keyword: first stack ≥2 wires via chained .workplane(offset=h) + 2D shapes, then call .loft(ruled=False). Calling loft with <2 pending wires raises 'Nothing to loft'.",
    "Workplane.twistExtrude": "twistExtrude(distance: float, angleDegrees: float) — positional args only; there is no `twistAngle` keyword.",
    "Workplane.parametricCurve": "parametricCurve(func, N=400, start=0, stop=1, ...) — there is no `step`/`startParam`/`end` keyword; func maps t∈[start,stop] to (x,y) or (x,y,z).",
}


def build_api_cards(path: Path = API_CARDS_PATH) -> list[dict]:
    """程序化抽取 Workplane 公开方法 + 手工增补卡，写 JSON 索引并返回。"""
    cards: dict[str, dict] = {}
    for name, member in inspect.getmembers(cq.Workplane, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        try:
            signature = f"Workplane.{name}{inspect.signature(member)}"
        except (TypeError, ValueError):
            signature = f"Workplane.{name}(...)"
        doc = inspect.getdoc(member) or ""
        # 只保留 docstring 首段（约 500 字符），控制卡片体积
        first_para = doc.split("\n\n")[0].strip()[:500]
        card = {
            "name": f"Workplane.{name}",
            "signature": signature,
            "doc": first_para,
        }
        if f"Workplane.{name}" in CONFUSION_NOTES:
            card["doc"] = (card["doc"] + " NOTE: " + CONFUSION_NOTES[f"Workplane.{name}"]).strip()
        cards[card["name"]] = card
    for extra in EXTRA_API_CARDS:
        cards[extra["name"]] = extra

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(cards.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    return list(cards.values())


def load_api_cards(path: Path = API_CARDS_PATH) -> dict[str, dict]:
    if not path.exists():
        build_api_cards(path)
    return {c["name"]: c for c in json.loads(path.read_text(encoding="utf-8"))}


def load_idiom_cards() -> list[dict]:
    from app.benchmarks.machinability_pilot.idiom_cards import IDIOM_CARDS

    return IDIOM_CARDS


# ---------------------------------------------------------------- 选择与分类


def select_static_cards(
    description: str, api_cards: dict[str, dict], idiom_cards: list[dict], max_api: int = 16
) -> dict[str, list[dict]]:
    """按描述意图做确定性检索：意图命中的卡优先，基础核心链殿后。"""
    text = description.lower()
    matched_api: list[str] = []
    idiom_ids: list[str] = []
    for pattern, api_hits, idiom_hits in INTENT_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            matched_api.extend(api_hits)
            idiom_ids.extend(idiom_hits)
    ordered = [n for n in dict.fromkeys(matched_api + BASE_API_NAMES)][:max_api]
    selected_api = [api_cards[n] for n in ordered if n in api_cards]
    selected_idioms = [c for c in idiom_cards if c["id"] in set(idiom_ids)]
    return {"api": selected_api, "idioms": selected_idioms}


ERROR_RULES: list[tuple[str, str, str | None]] = [
    # (错误正则, 失败类型, 惯用法卡 id)
    (r"unexpected keyword argument|got multiple values for argument", "api_misuse", None),
    (r"Nothing to loft|pending wires|More than one wire", "idiom", "loft_multi_section"),
    (r"has no attribute 'helix'|no attribute 'sin'|no attribute 'cos'|no attribute 'math'", "api_misuse", None),
    (r"not defined|Import statements are forbidden|__import__", "sandbox_contract", "sandbox_contract"),
    (r"no suitable edges for chamfer|Cannot find a solid", "idiom", "selector_basics"),
    (r"polyline|self-intersect|wires not planar", "idiom", "sampled_profile"),
    (r"BRep_API|StdFail|Standard_", "geometry_robustness", "geometry_robustness"),
]


def classify_error(error: str) -> dict:
    """错误 → (失败类型, 命中的 API 名, 惯用法卡 id)。供分类反馈检索修复知识。"""
    for pattern, kind, idiom_id in ERROR_RULES:
        if re.search(pattern, error, re.IGNORECASE):
            api_target = None
            match = re.search(r"(Workplane\.\w+|cq\.\w+(?:\.\w+)*)", error)
            if match:
                api_target = match.group(1)
            return {"kind": kind, "api_target": api_target, "idiom_id": idiom_id}
    return {"kind": "unknown", "api_target": None, "idiom_id": None}


def repair_knowledge(error: str, api_cards: dict[str, dict], idiom_cards: list[dict]) -> str:
    """分类反馈：按错误类型组装修复知识块（无命中返回空串）。"""
    hit = classify_error(error)
    sections: list[str] = []
    by_id = {c["id"]: c for c in idiom_cards}
    if hit["kind"] == "api_misuse":
        target = hit["api_target"] or ""
        card = api_cards.get(target)
        if card is None:  # 模型写了不存在的 API：找同类名前缀的卡片
            candidates = [v for k, v in api_cards.items() if k.lower().endswith("." + target.split(".")[-1].lower())]
            card = candidates[0] if candidates else None
        if card:
            sections.append(f"[Correct API] {card['signature']}\n{card['doc']}")
    if hit["idiom_id"] and hit["idiom_id"] in by_id:
        card = by_id[hit["idiom_id"]]
        sections.append(f"[Idiom: {card['title']}]\n{card['content']}")
    if hit["kind"] == "api_misuse" and not sections:
        sections.append("[Constraint] Use only real CadQuery APIs. Do not invent methods or keyword arguments.")
    return "\n\n".join(sections)


def format_static_block(selected: dict[str, list[dict]]) -> str:
    """静态注入块：API 卡 + 惯用法卡的紧凑排版。"""
    parts: list[str] = []
    if selected["api"]:
        api_lines = [f"- {c['signature']}" + (f" — {c['doc']}" if c.get("doc") else "") for c in selected["api"]]
        parts.append("[Available CadQuery APIs — use ONLY these, real signatures]\n" + "\n".join(api_lines))
    if selected["idioms"]:
        for c in selected["idioms"]:
            parts.append(f"[Idiom: {c['title']}]\n{c['content']}")
    return "\n\n".join(parts)
