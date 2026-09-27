"""曲面惯用法技能库（人工编写，方法组件②）。

每张卡 = 几何模式 → 经典写法。内容是从自建真值代码的调试教训中蒸馏的
通用配方（不含任何真值案例原文，留出守护见 grounding.py 模块注释）。
errors 字段是分类反馈的触发子串；triggers 留作嵌入检索升级用。
"""

IDIOM_CARDS: list[dict] = [
    {
        "id": "loft_multi_section",
        "title": "异截面放样（loft）",
        "errors": ["Nothing to loft", "pending wires"],
        "content": (
            "loft() needs >=2 pending wires on the stack. Chain sections with .workplane(offset=h):\n"
            "```python\n"
            "result = (cq.Workplane('XY')\n"
            "          .circle(18.0)\n"
            "          .workplane(offset=12.0).ellipse(20.0, 8.0)\n"
            "          .workplane(offset=12.0).rect(20.0, 30.0)\n"
            "          .loft(ruled=False))\n"
            "```\n"
            "loft(ruled=True) gives straight ruled sides; default False gives smooth.\n"
            "There is NO `sections=`/`profiles=` keyword."
        ),
    },
    {
        "id": "helical_sweep",
        "title": "螺旋扫掠",
        "errors": ["has no attribute 'helix'"],
        "content": (
            "There is no Workplane.helix(). Build the helix wire explicitly, then sweep:\n"
            "```python\n"
            "helix = cq.Wire.makeHelix(pitch=20.0, height=40.0, radius=16.0)\n"
            "flight = (cq.Workplane('XZ', origin=(16.0, 0, 0))\n"
            "          .rect(8.0, 4.0)\n"
            "          .sweep(helix, isFrenet=True))\n"
            "core = cq.Workplane('XY').circle(8.0).extrude(40.0)\n"
            "result = core.union(flight)\n"
            "```\n"
            "The profile plane normal must match the helix start tangent (+X), hence 'XZ'."
        ),
    },
    {
        "id": "twisted_prism",
        "title": "扭转拉伸",
        "errors": ["twistExtrude"],
        "content": (
            "twistExtrude(distance, angleDegrees) — two POSITIONAL args, no twistAngle keyword:\n"
            "```python\n"
            "result = cq.Workplane('XY').rect(20.0, 20.0).twistExtrude(40.0, 90.0)\n"
            "```\n"
            "The profile is twisted by angleDegrees over the extrusion height."
        ),
    },
    {
        "id": "sampled_profile",
        "title": "参数曲线轮廓（波形/凸轮/翼型）",
        "errors": ["self-intersect", "wires not planar", "sin", "cos"],
        "content": (
            "math module is NOT available in the sandbox — compute curve points with plain\n"
            "arithmetic, or sample the curve into a polyline with the loop variable only:\n"
            "```python\n"
            "pts = []\n"
            "n = 96\n"
            "for i in range(n):\n"
            "    t = 6.2832 * i / n            # 2*pi*i/n, avoid exact 2*pi endpoint\n"
            "    r = 20.0 + 4.0 * (t - 3.1416) # replace sin/cos with linear/numeric form,\n"
            "    pts.append((r, t))            # or precompute values as literals\n"
            "```\n"
            "For closed contours, NEVER append a point equal to the start point: .close()\n"
            "closes the wire itself; duplicated endpoints create degenerate edges.\n"
            "Keep the whole profile on one side of the closing line (no self-intersections)."
        ),
    },
    {
        "id": "sandbox_contract",
        "title": "沙箱契约",
        "errors": ["math is not defined", "Import statements are forbidden", "__import__", "not defined"],
        "content": (
            "The execution sandbox pre-injects only `cq` (cadquery). Hard rules:\n"
            "1. NO import statements at all (they are rejected before execution).\n"
            "2. `math` is NOT available — use plain arithmetic or precomputed literals.\n"
            "3. The final solid must be assigned to a variable named `result`.\n"
            "4. Available builtins: range, len, zip, list, dict, float, int, abs-free arithmetic."
        ),
    },
    {
        "id": "selector_basics",
        "title": "边/面选择器与圆角链",
        "errors": ["no suitable edges", "Cannot find a solid"],
        "content": (
            "Selectors operate on the current stack:\n"
            "```python\n"
            "body = cq.Workplane('XY').box(60.0, 40.0, 16.0).edges('|Z').fillet(4.0)\n"
            "body = body.faces('>Z').chamfer(1.5)          # chamfer BEFORE cutting slots\n"
            "cutter = (cq.Workplane('XY', origin=(0, 0, 16.0))\n"
            "          .rect(12.0, 6.0).extrude(-8.0)\n"
            "          .edges('|Z').fillet(1.5))\n"
            "result = body.cut(cutter)                      # cut pre-filleted cutters last\n"
            "```\n"
            "Chamfer/fillet the outer boundary first, then boolean-cut features —\n"
            "chamfering edges near cut features often fails in OCCT."
        ),
    },
    {
        "id": "hole_array",
        "title": "孔阵",
        "errors": ["rarray"],
        "content": (
            "```python\n"
            "result = (cq.Workplane('XY').box(60.0, 40.0, 10.0)\n"
            "          .faces('>Z').workplane()\n"
            "          .rarray(12.0, 12.0, 3, 2)   # spacingX, spacingY, countX, countY\n"
            "          .hole(4.0))                 # drills through everything below\n"
            "```"
        ),
    },
    {
        "id": "ubend_revolve",
        "title": "U 型弯管（旋转成型优先）",
        "errors": ["BRep_API", "command not done"],
        "content": (
            "Sweeping a circle along a semicircle path often degenerates. Prefer revolve:\n"
            "```python\n"
            "duct = (cq.Workplane('XZ', origin=(30.0, 0, 0))\n"
            "        .circle(8.0)\n"
            "        .revolve(180, (-30.0, 0), (-30.0, 1)))   # axis = global Z through origin\n"
            "```\n"
            "The revolve axis points are in WORKPLANE-local coordinates; the profile circle\n"
            "center is at distance = bend radius from that axis. End caps are open circles —\n"
            "union flange discs on the same planes to close."
        ),
    },
    {
        "id": "sweep_profile",
        "title": "沿路径扫掠",
        "errors": ["Cannot convert object type", "No pending wires"],
        "content": (
            "```python\n"
            "path = cq.Workplane('XY').moveTo(0, 0).threePointArc((30.0, 30.0), (60.0, 0))\n"
            "duct = cq.Workplane('YZ').circle(8.0).sweep(path)\n"
            "```\n"
            "The profile plane normal must align with the path start tangent.\n"
            "Avoid radiusArc when chord == 2*radius (exact semicircle degenerates);\n"
            "use threePointArc with an explicit mid point instead."
        ),
    },
    {
        "id": "revolve_profile",
        "title": "旋转体",
        "errors": ["revolve"],
        "content": (
            "```python\n"
            "result = (cq.Workplane('XZ')\n"
            "          .polyline([(2.0, 0.0), (20.0, 0.0), (16.0, 12.0), (2.0, 12.0)])\n"
            "          .close()\n"
            "          .revolve(360, (0, 0), (0, 1)))   # axis in local coords: (0,0)->(0,1)\n"
            "```\n"
            "The profile must NOT cross the revolve axis. Axis points are workplane-local."
        ),
    },
    {
        "id": "geometry_robustness",
        "title": "OCCT 构造稳健性清单",
        "errors": ["BRep_API", "StdFail", "Standard_"],
        "content": (
            "Common OCCT failures and fixes:\n"
            "1. Fillet radius must be < half the adjacent wall thickness.\n"
            "2. Avoid near-zero-length edges: never sample a closed curve hitting its\n"
            "   start point exactly (sin(2*pi*n) is ~1e-16, not 0) — dedupe endpoints.\n"
            "3. Avoid exact-degenerate arcs (semicircle via radiusArc) — use threePointArc.\n"
            "4. Cutters should fully protrude beyond the target face (add overshoot).\n"
            "5. Union parts that OVERLAP slightly rather than merely touch at a face."
        ),
    },
]
