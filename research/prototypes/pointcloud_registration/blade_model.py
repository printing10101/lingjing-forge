"""Parametric NACA blade generator for the symmetry probe.

The blade is a loft of NACA 4-digit sections along +Z.  Two variants matter:

* symmetric : NACA 0012, constant chord, no twist -> exact pressure/suction symmetry
* cambered  : NACA 2412, constant chord, no twist -> approximate symmetry

Both are exported as STEP so the registration probe reads the same format as
the rest of the project.
"""

from __future__ import annotations

import math
from pathlib import Path

import cadquery as cq
import numpy as np


def naca4_loop(
    m: float = 0.0,
    p: float = 0.4,
    t: float = 0.12,
    count: int = 40,
) -> np.ndarray:
    """Closed NACA 4-digit profile in chord-normalized coordinates."""
    x = np.linspace(0.0, 1.0, count)
    yt = 5.0 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1015 * x**4)
    if m > 0:
        yc = np.where(
            x < p,
            m / p**2 * (2.0 * p * x - x**2),
            m / (1.0 - p) ** 2 * ((1.0 - 2.0 * p) + 2.0 * p * x - x**2),
        )
    else:
        yc = np.zeros_like(x)

    upper = np.column_stack([x, yc + yt])
    lower = np.column_stack([x[::-1], (yc - yt)[::-1]])
    # Drop duplicated leading/trailing-edge points from the lower run.
    return np.vstack([upper, lower[1:-1]])


def make_blade(
    *,
    m: float = 0.0,
    p: float = 0.4,
    t: float = 0.12,
    span: float = 60.0,
    chord: float = 40.0,
    twist_deg: float = 0.0,
    taper: float = 0.0,
    sections: int = 8,
) -> cq.Solid:
    """Build a lofted blade; taper=0 and twist=0 gives a uniform blade."""
    base = naca4_loop(m=m, p=p, t=t)
    wires = []
    for index in range(sections):
        fraction = index / (sections - 1)
        z = span * fraction
        local_chord = chord * (1.0 - taper * fraction)
        twist = math.radians(twist_deg * fraction)
        rotation = np.array([[math.cos(twist), -math.sin(twist)], [math.sin(twist), math.cos(twist)]])
        points = (base * local_chord) @ rotation.T
        wire = cq.Wire.makePolygon(
            [cq.Vector(float(x), float(y), float(z)) for x, y in points],
            close=True,
        )
        wires.append(wire)
    solid = cq.Solid.makeLoft(wires, ruled=False)
    if not solid.isValid():
        raise RuntimeError("generated blade is not a valid B-rep")
    return solid


def export_blade(path: Path, **kwargs) -> Path:
    solid = make_blade(**kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(cq.Workplane("XY").newObject([solid]), str(path))
    return path
