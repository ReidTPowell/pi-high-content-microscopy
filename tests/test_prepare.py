import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_prepare", ROOT / "hca_prepare.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


class PrepareTests(unittest.TestCase):
    def test_build_config_records_blinded_contract_without_modifying_template(self):
        template = {"name": "test", "input": {"adapter": "hcsai"}, "channels": {"0": {}},
                    "analysis": {"unit_of_analysis": "well", "optimization": {"mode": "human"}}}
        with tempfile.TemporaryDirectory() as temp:
            result = PREPARE.build_config(template, Path(temp), "automated", None, True, None)
        self.assertEqual(template["analysis"]["optimization"]["mode"], "human")
        self.assertEqual(result["analysis"]["optimization"]["mode"], "automated")
        self.assertTrue(result["assay_contract"]["segmentation_optimization_blinded"])
        self.assertEqual(result["assay_contract"]["biological_endpoint"], "pending")

    def test_next_version_ignores_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "assay-001.json").write_text("{}")
            (directory / "notes.json").write_text("{}")
            self.assertEqual(PREPARE.next_version(directory, "assay"), 2)
