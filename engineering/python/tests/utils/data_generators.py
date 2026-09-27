"""Test data generators for the Lingjing Manufacturing test framework."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Optional


def generate_test_stl(filepath: str, part_name: str = "test_part") -> str:
    """Generate a minimal binary STL file for testing."""
    import struct

    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    header = b"Lingjing Test STL - " + part_name.encode().ljust(80, b"\0")
    num_triangles = 8
    header += struct.pack("<I", num_triangles)

    vertices = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
        (0.0, 0.0, 5.0),
        (10.0, 0.0, 5.0),
        (10.0, 10.0, 5.0),
        (0.0, 10.0, 5.0),
    ]

    triangles = [
        (0, 1, 2),
        (0, 2, 3),
        (4, 6, 5),
        (4, 7, 6),
        (0, 5, 1),
        (0, 4, 5),
        (2, 7, 3),
        (2, 6, 7),
    ]

    with open(filepath, "wb") as f:
        f.write(header)
        for tri in triangles:
            v0, v1, v2 = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
            ax, ay, az = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
            bx, by, bz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
            nx = ay * bz - az * by
            ny = az * bx - ax * bz
            nz = ax * by - ay * bx
            length = (nx * nx + ny * ny + nz * nz) ** 0.5
            if length > 0:
                nx, ny, nz = nx / length, ny / length, nz / length
            f.write(struct.pack("<3f", nx, ny, nz))
            for v in [v0, v1, v2]:
                f.write(struct.pack("<3f", *v))
            f.write(struct.pack("<H", 0))

    return str(filepath)


def generate_geometric_test_data(
    output_dir: str,
    num_circles: int = 5,
    num_polygons: int = 3,
) -> dict:
    """Generate random geometric test data for geometry operation tests."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(42)
    circles = []
    for _ in range(num_circles):
        circle = {
            "center_x": round(random.uniform(-50, 50), 3),
            "center_y": round(random.uniform(-50, 50), 3),
            "radius": round(random.uniform(1, 30), 3),
        }
        circles.append(circle)

    polygons = []
    for _ in range(num_polygons):
        num_verts = random.randint(3, 8)
        angles = sorted([random.uniform(0, 2 * 3.14159) for _ in range(num_verts)])
        radius = random.uniform(5, 25)
        cx = random.uniform(-30, 30)
        cy = random.uniform(-30, 30)
        vertices = [
            [round(cx + radius * math.cos(a), 3), round(cy + radius * math.sin(a), 3)]
            for a in angles
        ]
        polygons.append({"vertices": vertices, "num_vertices": num_verts})

    data = {
        "circles": circles,
        "polygons": polygons,
        "metadata": {
            "num_circles": num_circles,
            "num_polygons": num_polygons,
            "generated_by": "test_data_generator",
        },
    }

    output_file = output_dir / "geometric_test_data.json"
    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)

    return data


def generate_regression_baseline(
    output_dir: str,
    part_id: str,
    expected_gcode: str,
) -> str:
    """Save a G-code baseline for regression testing."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline = {
        "part_id": part_id,
        "gcode": expected_gcode,
        "version": "1.0.0",
        "created_at": "2026-01-01T00:00:00Z",
    }

    baseline_file = output_dir / f"baseline_{part_id}.json"
    with open(baseline_file, "w") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)

    return str(baseline_file)


def create_mock_model(
    dimensions: Optional[dict] = None, features: Optional[dict] = None
) -> dict:
    """Create a mock reconstructed 3D model for geometric validation tests."""
    if dimensions is None:
        dimensions = {
            "length": 100.0,
            "width": 50.0,
            "height": 30.0,
            "hole_diameter": 10.0,
        }
    if features is None:
        features = {
            "main_body": {"confidence": 0.98, "iou": 0.97},
            "through_hole": {"confidence": 0.95, "iou": 0.94},
            "counterbore": {"confidence": 0.92, "iou": 0.91},
        }
    return {
        "dimensions": dimensions,
        "features": features,
        "topology": [
            {
                "feature_a": "main_body",
                "feature_b": "through_hole",
                "relation": "contains",
            },
            {
                "feature_a": "main_body",
                "feature_b": "counterbore",
                "relation": "contains",
            },
        ],
    }
