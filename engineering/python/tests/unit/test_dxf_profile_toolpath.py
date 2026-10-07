"""DXF → NC 主路径产出真实轮廓/型腔刀轨的端到端验收测试。

为什么单独一个文件（2026-10-07 全库定级发现的病根）：
定级实测中，20 个 fixture 的 `刀轨策略` 注释**全是 `face_raster`**（0/20 命中
pocket/profile）——`PlanarToolpathEngine.pocket()/profile()`（多边形偏置、自交
分解、螺旋下刀）只活在单测和 benchmark pilot 里，产品主路径从没调用过。
后果：case15 椭圆轮廓、case13 圆角矩形这类「外形就是零件」的图纸，程序只铣平面
+ 钻孔，照它加工出来是一块方板，不是图上的零件。

本文件把「主路径必须切出真实外形/内腔」钉成回归门禁：
1. 外轮廓 → `profile_offset` 刀轨，且走刀顶点来自 DXF 几何（不是包络矩形）
2. 内环（用 LWPOLYLINE 表达的内孔/窗洞）→ `pocket` 刀轨，不再 0 工序漏掉
3. 面铣扫描范围跟随零件真实包络（此前基准面尺寸硬编码 200×100）
4. 钻孔循环不被轮廓桥重复生成成挖槽（同一孔不能既钻又铣）

fixture 用仓库真实图纸，断言只看 NC 文本里的策略标记与坐标数值，不比对快照全文。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.dxf.process_service import DxfProcessService

# 必须用绝对路径：CI 的工作目录与本机不同，写成 Path("data/test_fixtures") 会让
# 本文件全部用例在 CI 上静默 SKIPPED（2026-10-07 首次推送后就是这样——本地 19/20
# 的证据在 CI 上其实一条都没跑）。tests/unit → tests → python → engineering → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = REPO_ROOT / "data" / "test_fixtures"


@pytest.mark.unit
def test_fixture_directory_is_resolvable():
    """路径解析守护：解析不到 fixture 目录要报错，不能让整套验收静默跳过。"""
    assert FIXTURES.is_dir(), f"fixture 目录解析失败: {FIXTURES}"


def _run(tmp_path: Path, case: str) -> tuple[str, object]:
    """跑一个 fixture，返回 (NC 文本, gcode 阶段结果)。"""
    src = FIXTURES / f"{case}.dxf"
    if not src.exists():  # pragma: no cover - fixture 缺失属环境异常
        pytest.skip(f"fixture 缺失: {src}")
    out = tmp_path / case
    result = DxfProcessService().process(dxf_path=src, output_dir=out, postprocessor="fanuc_0i")
    assert result.parse.success, result.parse.error
    assert result.features.success, result.features.error
    assert result.gcode is not None and result.gcode.success, result.gcode.error if result.gcode else "无 gcode 阶段"
    nc = next(iter(out.glob("*.nc")))
    return nc.read_text(encoding="utf-8"), result.gcode


def _strategies(text: str) -> list[str]:
    return re.findall(r"刀轨策略: ([a-z_]+)", text)


def _cutting_xy(text: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for m in re.finditer(r"^G0?1\s+X(-?[\d.]+)\s+Y(-?[\d.]+)", text, re.M):
        pts.append((float(m.group(1)), float(m.group(2))))
    return pts


def _op_blocks(text: str) -> dict[str, list[str]]:
    """按 `; ---- OPxx` 切分程序体，返回 {工序名: [该工序的指令行]}。"""
    blocks: dict[str, list[str]] = {}
    current = "HEADER"
    blocks[current] = []
    for line in text.splitlines():
        m = re.match(r"^; ---- OP\d+\s+(.*)", line)
        if m:
            current = m.group(1).strip()
            blocks.setdefault(current, [])
            continue
        blocks.setdefault(current, []).append(line)
    return blocks


def _strategy_of(block_lines: list[str]) -> str | None:
    for line in block_lines:
        m = re.search(r"刀轨策略: ([a-z_]+)", line)
        if m:
            return m.group(1)
    return None


class TestProfileOnMainPath:
    @pytest.mark.unit
    def test_rounded_rect_outline_is_profiled(self, tmp_path: Path):
        """圆角矩形（外轮廓即零件）必须产出 profile 刀轨。"""
        text, _ = _run(tmp_path, "case13_rounded_rect")
        strategies = _strategies(text)

        assert any("profile" in s for s in strategies), f"未走外形轮廓铣: {strategies}"

    @pytest.mark.unit
    def test_ellipse_outline_is_profiled(self, tmp_path: Path):
        """椭圆轮廓零件：外形必须被切削，且走刀点数跟随采样密度（不是 4 角矩形）。"""
        text, _ = _run(tmp_path, "case15_ellipse_outline")
        strategies = _strategies(text)
        pts = _cutting_xy(text)

        assert any("profile" in s for s in strategies), f"未走外形轮廓铣: {strategies}"
        assert len(pts) > 40, f"切削点过少，疑似按矩形包络走刀: {len(pts)}"

    @pytest.mark.unit
    def test_line_built_box_is_profiled(self, tmp_path: Path):
        """用 4 条 LINE 画的矩形（无多段线）也必须能链化成轮廓并切削。"""
        text, _ = _run(tmp_path, "case1_simple_box")
        strategies = _strategies(text)

        assert any("profile" in s for s in strategies), f"LINE 轮廓未进切削主路径: {strategies}"

    @pytest.mark.unit
    def test_face_milling_follows_real_bbox_not_hardcoded(self, tmp_path: Path):
        """面铣必须落在零件真实包络内：跨度对、位置也要对。

        两层病根先后修掉：
        1) `_build_features` 把基准面尺寸写死 length=200/width=100 → 所有零件
           都扫同一个 200×100 区域；
        2) 改成传包络角点后，引擎按 anchor="center" 解释坐标，于是整张面铣
           往 −L/2,−W/2 平移了半个零件（case6 实测 83% 运动点跑到毛坯网格外）。
        所以这里同时断言跨度与中心。
        """
        src = FIXTURES / "case2_box_4holes.dxf"
        out = tmp_path / "case2"
        result = DxfProcessService().process(dxf_path=src, output_dir=out, postprocessor="fanuc_0i")
        assert result.gcode is not None and result.gcode.success
        text = next(out.glob("*.nc")).read_text(encoding="utf-8")
        fs = result.features.summary or {}

        raster_lines = [lines for _, lines in _op_blocks(text).items() if _strategy_of(lines) == "face_raster"]
        assert raster_lines, "未找到 face_raster 工序"
        body = "\n".join(raster_lines[0])
        xs = [float(m.group(1)) for m in re.finditer(r"^G0?1\s+X(-?[\d.]+)", body, re.M)]
        ys = [float(m.group(1)) for m in re.finditer(r"Y(-?[\d.]+)", body)]
        assert xs and ys, "面铣无切削坐标"

        part_min_x = float(fs["overall_min_x"])
        part_len = float(fs["overall_length"])
        part_wid = float(fs["overall_width"])
        part_cx = part_min_x + part_len / 2.0
        part_cy = float(fs["overall_min_y"]) + part_wid / 2.0

        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        # 跨度：应贴合零件（面铣按包络扫描，旧口径是固定 200）
        assert span_x <= part_len + 1e-6, f"面铣 X 跨度 {span_x} 超出零件 {part_len}（疑似硬编码毛坯）"
        assert span_x >= 0.7 * part_len, f"面铣 X 覆盖不足: {span_x} vs {part_len}"
        assert span_y >= 0.7 * part_wid, f"面铣 Y 覆盖不足: {span_y} vs {part_wid}"
        # 位置：扫描中心必须落在零件中心附近（角点/中心混用会偏半个零件）
        assert abs((max(xs) + min(xs)) / 2.0 - part_cx) <= 0.15 * part_len, (
            f"面铣中心 X 偏移：程序 {(max(xs) + min(xs)) / 2.0:.1f} vs 零件 {part_cx:.1f}"
        )
        assert abs((max(ys) + min(ys)) / 2.0 - part_cy) <= 0.15 * part_wid, (
            f"面铣中心 Y 偏移：程序 {(max(ys) + min(ys)) / 2.0:.1f} vs 零件 {part_cy:.1f}"
        )


class TestPocketOnMainPath:
    @pytest.mark.unit
    def test_polyline_inner_loop_is_milled(self, tmp_path: Path):
        """内环用 LWPOLYLINE 表达的孔（case4）必须产出挖槽工序，不再 0 工序。"""
        text, gcode = _run(tmp_path, "case4_polyline_outer_with_hole")
        strategies = _strategies(text)
        summary = gcode.summary or {}

        assert summary.get("holes", 0) == 0, "内环是多段线，不该被当成圆孔"
        assert any("pocket" in s for s in strategies), f"内环未生成挖槽刀轨: {strategies}"

    @pytest.mark.unit
    def test_rect_block_inner_loops_become_pocket_operations(self, tmp_path: Path):
        """两个内环必须进工序表（此前 0 工序被静默丢掉）。

        窄槽（内切尺寸 < 刀具直径）会被刀轨引擎**正当拒绝**并回退到模板走线，
        给出 E4001 与换刀/改工艺的 actionable 建议——那是正确行为，不是 bug。
        本用例只钉住"内环不再消失"这一点：工序名里必须出现 POCKET_。
        """
        text, gcode = _run(tmp_path, "case9_rect_block")
        summary = gcode.summary or {}
        ops = list(_op_blocks(text))

        assert any("POCKET_" in name for name in ops), f"内环没有对应工序: {ops}"
        assert sum(1 for name in ops if "POCKET_" in name) == 2
        assert summary.get("operations", 0) >= 4

    @pytest.mark.unit
    def test_narrow_pocket_refusal_is_reported_not_silent(self, tmp_path: Path):
        """引擎拒绝窄槽环切时，原因必须写进 NC 程序（不允许静默退成模板走线）。"""
        text, _ = _run(tmp_path, "case9_rect_block")
        pocket_blocks = [lines for name, lines in _op_blocks(text).items() if "POCKET_" in name]

        assert pocket_blocks, "无型腔工序"
        body = "\n".join(pocket_blocks[0])
        if "刀轨策略: pocket" in body:
            return  # 环切成功，无需拒绝说明
        assert "E4001" in body, f"引擎回退但程序里没有原因说明：{body[:300]}"
        assert "模板走线" in body


class TestDrillingNotDuplicated:
    @pytest.mark.unit
    def test_circular_holes_stay_drilled_not_pocketed(self, tmp_path: Path):
        """25 个圆孔：必须仍是 25 个钻孔循环，且不被轮廓桥复制成 25 个挖槽。"""
        text, gcode = _run(tmp_path, "case11_perforated_plate")
        summary = gcode.summary or {}
        # 钻孔循环在本仓后处理器里以模态 G98/G99 前缀输出（如 `G98 G73 X.. Y..`），
        # 锚定 ^G73 会零命中——按循环码 token 统计。
        drill_cycles = len(re.findall(r"\bG(?:73|81|83)\b", text))

        assert summary.get("holes") == 25
        assert drill_cycles == 25, f"钻孔循环数与孔数不符: {drill_cycles}"
        # 外轮廓可以铣，但不得为每个圆孔生成挖槽（工序数 = 面铣 + 外形 + 25 钻）
        ops = len(re.findall(r"^; ---- OP", text, re.M))
        assert ops <= 28, f"工序数膨胀（孔被重复挖槽？）: {ops}"
