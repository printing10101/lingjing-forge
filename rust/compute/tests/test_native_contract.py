"""Run after cargo build: python rust/compute/tests/test_native_contract.py.

Load the just-built library without replacing the installed compute package.
"""

import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np


def load_native():
    directory = Path(__file__).resolve().parents[1] / "target" / "debug"
    filename = {
        "win32": "compute_pyo3_bindings.dll",
        "darwin": "libcompute_pyo3_bindings.dylib",
    }.get(sys.platform, "libcompute_pyo3_bindings.so")
    library = directory / filename
    if not library.is_file():
        raise RuntimeError("Build compute-pyo3-bindings before running native contract tests")
    loader = importlib.machinery.ExtensionFileLoader("_native", str(library))
    spec = importlib.util.spec_from_file_location("_native", library, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = load_native()
        cls.cutter = cls.native.voxel_cutter

    def test_public_names_and_version(self):
        self.assertTrue(self.native.is_available())
        self.assertEqual(self.native.compute_core_version, self.native.__version__)
        for name in ("build_tool_mask", "apply_tool_mask", "discretize_linear_segment", "benchmark_mask_cut"):
            self.assertTrue(callable(getattr(self.cutter, name)))

    def test_long_tool_mask_preserves_cutting_length(self):
        mask, info = self.cutter.build_tool_mask("flat", 4.0, 0.0, 20.0, 1.0)
        center = np.array(mask.shape) // 2
        self.assertEqual(mask.shape, (info["nx"], info["ny"], info["nz"]))
        self.assertTrue(mask[center[0], center[1], center[2] - 20])

    def test_form_profile_can_be_borrowed_from_python(self):
        mask, _ = self.cutter.build_tool_mask(
            "form", 6.0, 0.0, 10.0, 1.0,
            form_profile=[(-10.0, 1.0), (0.0, 3.0)],
        )
        self.assertTrue(mask.any())

    def test_cutting_writes_back_into_numpy_grid(self):
        grid = np.ones((5, 5, 5), dtype=bool)
        mask = np.ones((1, 1, 1), dtype=bool)
        result = self.cutter.apply_tool_mask(
            grid, mask, np.array([[2.0, 2.0, 2.0]]), (0.0, 0.0, 0.0), 1.0, 0.0,
        )
        self.assertEqual(result["removed"], 1)
        self.assertFalse(grid[2, 2, 2])
        self.assertEqual(np.count_nonzero(grid), 124)

    def test_wrong_point_width_is_a_validation_error(self):
        for width in (2, 4):
            with self.subTest(width=width), self.assertRaises(ValueError):
                self.cutter.apply_tool_mask(
                    np.ones((3, 3, 3), dtype=bool), np.ones((1, 1, 1), dtype=bool),
                    np.zeros((1, width)), (0.0, 0.0, 0.0), 1.0, 0.0,
                )

    def test_invalid_tool_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            self.cutter.build_tool_mask("tapered", 10.0, 0.0, 20.0, 1.0, taper_angle_deg=float("nan"))


if __name__ == "__main__":
    unittest.main()
