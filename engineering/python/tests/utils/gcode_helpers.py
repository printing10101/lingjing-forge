"""Test utility functions for the Lingjing Manufacturing test framework."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class GCodeDiffResult:
    """Result of a G-code comparison."""

    is_within_tolerance: bool
    differences: list[str]
    match_score: float
    total_commands_compared: int
    matched_commands: int


def point_in_circle(px: float, py: float, cx: float, cy: float, r: float) -> bool:
    """Check if a point is inside a circle."""
    dist_sq = (px - cx) ** 2 + (py - cy) ** 2
    return dist_sq <= r**2


def point_in_polygon(px: float, py: float, vertices: list[tuple[float, float]]) -> bool:
    """Check if a point is inside a polygon using ray-casting algorithm."""
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def circle_polygon_intersection_area(
    circle_cx: float,
    circle_cy: float,
    circle_r: float,
    vertices: list[tuple[float, float]],
) -> float:
    """Estimate circle-polygon intersection area using Monte Carlo sampling.

    The estimator uses a deterministic, well-distributed pseudo-random
    number generator (a SplitMix64 stream seeded from the call) so the
    result is reproducible across runs and across operating systems
    while still being uniformly distributed across the bounding box.
    """
    samples = 10000
    x_min = min(v[0] for v in vertices) - circle_r
    x_max = max(v[0] for v in vertices) + circle_r
    y_min = min(v[1] for v in vertices) - circle_r
    y_max = max(v[1] for v in vertices) + circle_r

    bbox_area = (x_max - x_min) * (y_max - y_min)
    if bbox_area == 0:
        return 0.0

    # Seed SplitMix64 from a stable hash of the inputs so that the
    # estimate is reproducible while still being well-distributed.
    seed = (
        abs(hash((circle_cx, circle_cy, circle_r, tuple(vertices)))) or 0x9E3779B9
    ) & 0xFFFFFFFFFFFFFFFF

    state = [seed]
    for _ in range(2):
        state.append((state[-1] + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF)

    def _next() -> float:
        state[0] = (state[0] + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = state[0]
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        z = z ^ (z >> 31)
        return (z & 0xFFFFFFFFFFFFFFFF) / float(1 << 64)

    inside_both = 0
    inside_polygon = 0

    for _ in range(samples):
        px = x_min + _next() * (x_max - x_min)
        py = y_min + _next() * (y_max - y_min)

        in_poly = point_in_polygon(px, py, vertices)
        if in_poly:
            inside_polygon += 1
            if point_in_circle(px, py, circle_cx, circle_cy, circle_r):
                inside_both += 1

    if inside_polygon == 0:
        return 0.0

    poly_area_estimate = (inside_polygon / samples) * bbox_area
    intersection_fraction = inside_both / inside_polygon
    return poly_area_estimate * intersection_fraction


def parse_gcode_commands(gcode: str) -> list[dict]:
    """Parse G-code string into structured commands for comparison."""
    commands = []
    for line in gcode.strip().split("\n"):
        line = line.strip()
        if (
            not line
            or line.startswith("%")
            or line.startswith("(")
            or line.startswith(";")
        ):
            continue
        cmd = {"raw": line, "g_codes": [], "m_codes": [], "coords": {}, "feeds": {}}

        for match in re.finditer(r"([Gg])(\d+\.?\d*)", line):
            cmd["g_codes"].append(f"G{match.group(2)}")

        for match in re.finditer(r"([Mm])(\d+\.?\d*)", line):
            cmd["m_codes"].append(f"M{match.group(2)}")

        for match in re.finditer(r"([XYZ])([-+]?\d*\.?\d+)", line):
            cmd["coords"][match.group(1)] = float(match.group(2))

        for match in re.finditer(r"([FS])(\d+\.?\d*)", line):
            cmd["feeds"][match.group(1)] = float(match.group(2))

        commands.append(cmd)
    return commands


def compare_gcode(
    gcode1: str, gcode2: str, tolerance: Optional[dict] = None
) -> GCodeDiffResult:
    """Compare two G-code outputs and report differences within tolerance."""
    if tolerance is None:
        tolerance = {
            "coordinate_precision": 0.01,
            "feed_rate_tolerance_percent": 5.0,
            "spindle_speed_tolerance_percent": 2.0,
            "ignore_comments": True,
            "ignore_program_numbers": True,
            "ignore_timestamps": True,
        }

    cmds1 = parse_gcode_commands(gcode1)
    cmds2 = parse_gcode_commands(gcode2)

    differences = []
    total = max(len(cmds1), len(cmds2))
    matched = 0
    coord_tol = tolerance.get("coordinate_precision", 0.01)
    feed_tol_pct = tolerance.get("feed_rate_tolerance_percent", 5.0)

    for i in range(total):
        if i >= len(cmds1):
            differences.append(f"Line {i}: Missing in gcode1")
            continue
        if i >= len(cmds2):
            differences.append(f"Line {i}: Missing in gcode2")
            continue

        c1 = cmds1[i]
        c2 = cmds2[i]

        if c1["g_codes"] != c2["g_codes"] or c1["m_codes"] != c2["m_codes"]:
            differences.append(f"Line {i}: Code mismatch: {c1['raw']} vs {c2['raw']}")
            continue

        coord_diff = False
        for axis in set(c1["coords"].keys()) | set(c2["coords"].keys()):
            v1 = c1["coords"].get(axis, 0.0)
            v2 = c2["coords"].get(axis, 0.0)
            if abs(v1 - v2) > coord_tol:
                coord_diff = True
                differences.append(f"Line {i}: {axis} coord diff: {v1:.3f} vs {v2:.3f}")

        feed_diff = False
        for key in set(c1["feeds"].keys()) | set(c2["feeds"].keys()):
            v1 = c1["feeds"].get(key, 0.0)
            v2 = c2["feeds"].get(key, 0.0)
            if v1 > 0 and (abs(v1 - v2) / v1 * 100) > feed_tol_pct:
                feed_diff = True
                differences.append(f"Line {i}: {key} feed diff: {v1:.1f} vs {v2:.1f}")

        if not coord_diff and not feed_diff:
            matched += 1

    match_score = matched / total if total > 0 else 0.0
    return GCodeDiffResult(
        is_within_tolerance=len(differences) == 0,
        differences=differences,
        match_score=match_score,
        total_commands_compared=total,
        matched_commands=matched,
    )


def generate_test_gcode(scenario: str = "simple_contour") -> str:
    """Generate test G-code for different machining scenarios."""
    if scenario == "simple_contour":
        return """%
O0001 (SIMPLE CONTOUR)
G21 G17 G40 G49 G80 G90 G94
G00 G91 G28 Z0.
G00 G90 G54 X0. Y0.
G00 G43 Z50.000 H00
M03 S8000
M08
G01 X10.000 Y0.000 F500.000
G01 X10.000 Y10.000 F500.000
G01 X0.000 Y10.000 F500.000
G01 X0.000 Y0.000 F500.000
M09
M05
G00 G91 G28 Z0.
G00 G91 G28 X0. Y0.
G90
M30
%"""
    elif scenario == "drilling":
        return """%
O0002 (DRILLING CYCLE)
G21 G17 G40 G49 G80 G90 G94
G00 G91 G28 Z0.
G00 G90 G54 X0. Y0.
G00 G43 Z50.000 H00
M03 S5000
M08
G98 G83 X10.000 Y10.000 Z-15.000 R2.000 Q3.000 F200.000
G98 G83 X30.000 Y10.000 Z-15.000 R2.000 Q3.000 F200.000
G98 G83 X20.000 Y30.000 Z-15.000 R2.000 Q3.000 F200.000
G80
M09
M05
G00 G91 G28 Z0.
G00 G91 G28 X0. Y0.
G90
M30
%"""
    elif scenario == "pocket_milling":
        return """%
O0003 (POCKET MILLING)
G21 G17 G40 G49 G80 G90 G94
G00 G91 G28 Z0.
G00 G90 G54 X0. Y0.
G00 G43 Z50.000 H00
M03 S6000
M08
G01 Z-2.000 F300.000
G01 X50.000 Y0.000 F800.000
G01 X50.000 Y50.000 F800.000
G01 X0.000 Y50.000 F800.000
G01 X0.000 Y0.000 F800.000
G00 Z2.000
G01 Z-4.000 F300.000
G01 X48.000 Y2.000 F600.000
G01 X48.000 Y48.000 F600.000
G01 X2.000 Y48.000 F600.000
G01 X2.000 Y2.000 F600.000
M09
M05
G00 G91 G28 Z0.
G00 G91 G28 X0. Y0.
G90
M30
%"""
    else:
        return generate_test_gcode("simple_contour")


def assert_almost_equal(
    actual: float, expected: float, tolerance: float = 1e-6, msg: str = ""
):
    """Assert two floats are almost equal within tolerance."""
    assert abs(actual - expected) <= tolerance, (
        f"{msg}: Expected {expected}, got {actual} (tolerance: {tolerance})"
    )
