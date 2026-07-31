import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
SPEC = importlib.util.spec_from_file_location("hca_metadata", ROOT / "hca_metadata.py")
METADATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METADATA)


class MetadataTests(unittest.TestCase):
    def test_loads_well_metadata_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp:
            plate_map = Path(temp) / "plate.csv"
            plate_map.write_text("well,treatment,dose\na01,vehicle,0\n", encoding="utf-8")
            index, columns = METADATA.load_plate_map(plate_map)
            self.assertEqual(index["A01"], {"treatment": "vehicle", "dose": "0"})
            self.assertEqual(columns, ["well", "treatment", "dose"])

    def test_rejects_duplicate_wells(self):
        with tempfile.TemporaryDirectory() as temp:
            plate_map = Path(temp) / "plate.csv"
            plate_map.write_text("well,treatment\nA01,a\na01,b\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                METADATA.load_plate_map(plate_map)
