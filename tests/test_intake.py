import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_intake", ROOT / "hca_intake.py")
INTAKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INTAKE)


class IntakeTests(unittest.TestCase):
    def test_describe_reduces_manifest_to_actionable_inventory(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(INTAKE, "build_manifest", return_value=[{"path": "image.tif"}]), patch.object(INTAKE, "manifest_summary", return_value={
                "images": 8, "wells": ["A01", "A02"], "sites": ["s0", "s1"], "channels": [0, 1],
                "timepoints": [0], "z_planes": [0], "adapters": {"hcsai": 8}}):
            result = INTAKE.describe(Path(temp))
        self.assertEqual(result["images"], 8)
        self.assertEqual(result["wells"], 2)
        self.assertEqual(result["channels"], [0, 1])
