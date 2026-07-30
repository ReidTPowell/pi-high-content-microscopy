import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATE = load("hca_validate")
CONFIG = {"name": "test", "input": {"adapter": "hcsai", "expected_channels": [0, 1], "expected_z_planes": [0, 1]}, "channels": {"0": {}, "1": {}}, "analysis": {"unit_of_analysis": "well"}}


def record(channel=0, z=0):
    return {"path": f"a_w{channel}_z{z}.tif", "format": "tiff", "adapter": "hcsai", "plate": None,
            "well": "A01", "row": "A", "column": 1, "site": "s0", "timepoint": 0,
            "channel": channel, "z": z, "prefix": "a"}


class ValidationTests(unittest.TestCase):
    def test_reports_missing_channel_and_z(self):
        report = VALIDATE.validate([record(0, 0)], CONFIG)
        self.assertFalse(report["ok"])
        self.assertEqual(report["incomplete_channel_fields"][0]["missing_channels"], [1])
        self.assertEqual(report["incomplete_z_fields"][0]["missing_z_planes"], [1])

    def test_rejects_malformed_record(self):
        bad = record()
        del bad["site"]
        report = VALIDATE.validate([bad], CONFIG)
        self.assertFalse(report["ok"])
        self.assertIn("record 0: missing site", report["contract_errors"])

    def test_accepts_multiz_multitimepoint_manifest(self):
        records = [record(channel, z) for channel in (0, 1) for z in (0, 1)]
        records.extend([{**entry, "timepoint": 1, "path": entry["path"] + "_t1"} for entry in records])
        self.assertTrue(VALIDATE.validate(records, CONFIG)["ok"])
