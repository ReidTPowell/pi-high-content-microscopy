import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_cellpose_tune", ROOT / "hca_cellpose_tune.py")
TUNE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TUNE)


class CellposeTuneTests(unittest.TestCase):
    def test_parses_auto_diameter_and_builds_bounded_grid(self):
        self.assertEqual(TUNE.values("auto,20", True), [None, 20.0])
        grid = TUNE.candidates([None, 20.0], [0.3], [-1.0, 0.0], 4)
        self.assertEqual(len(grid), 4)
        self.assertEqual(grid[0], {"diameter": None, "flow_threshold": 0.3, "cellprob_threshold": -1.0})

    def test_rejects_oversized_grid(self):
        with self.assertRaises(ValueError):
            TUNE.candidates([10.0, 20.0], [0.3, 0.4], [-1.0, 0.0], 2)
