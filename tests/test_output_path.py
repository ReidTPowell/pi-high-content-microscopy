import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_contract", ROOT / "hca_contract.py")
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)


class OutputPathTests(unittest.TestCase):
    def test_uses_barcode_level_sibling_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run2" / "70126" / "acquisition"
            root.mkdir(parents=True)
            (root / "run.jdce").write_text('{"PlateId": "70126"}', encoding="utf-8")
            self.assertEqual(CONTRACT.default_output_dir(root), root.parent.parent / "70126_piHCA")

    def test_uses_acquisition_name_without_barcode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "acquisition"
            root.mkdir()
            self.assertEqual(CONTRACT.default_output_dir(root), root.parent / "acquisition_piHCA")
