"""Synthetic drilling fixtures; never evidence for a real manufacturing approval."""


def drilling_limits():
    return {"schema_version": 1, "hardness_min_hrc": 0.0, "hardness_max_hrc": 24.0,
            "hardness_max_inclusive": False, "diameter_min_mm": 8.0, "diameter_max_mm": 8.0,
            "max_depth_to_diameter": 4.0, "coolants": ["flood"], "workholdings": ["vise"]}


def machining_conditions():
    return {"tool": "fixture_d8", "hardness_hrc": 20.0, "diameter_mm": 8.0,
            "depth_mm": 32.0, "coolant": "flood", "workholding": "vise"}
