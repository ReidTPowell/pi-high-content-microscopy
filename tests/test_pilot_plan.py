import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_pilot_plan", ROOT / "hca_pilot_plan.py")
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


class PilotPlanTests(unittest.TestCase):
    def test_selects_paired_fields_across_intensity_range(self):
        records = []
        for index, well in enumerate(("A01", "B01", "C01", "D01"), start=1):
            for channel in (0, 1):
                records.append({"well": well, "site": "s0", "timepoint": 0, "z": 0, "channel": channel,
                                "path": f"{well}-w{channel}.tif", "acquisition": {"MeanIntensity": str(index * 100 + channel)}})
        config = {"channels": {"0": {"role": "nucleus"}, "1": {"role": "cell_boundary"}},
                  "analysis": {"segmentation": {"nucleus": {"channel_role": "nucleus"},
                                                  "cell": {"enabled": True, "channel_role": "cell_boundary"}}}}
        selected = PILOT.select_fields(records, config, 3)
        self.assertEqual([item["well"] for item in selected], ["A01", "C01", "D01"])
        self.assertTrue(all(set(item["channels"]) == {"0", "1"} for item in selected))

    def test_rejects_unpaired_fields(self):
        config = {"channels": {"0": {"role": "nucleus"}, "1": {"role": "cell_boundary"}},
                  "analysis": {"segmentation": {"nucleus": {"channel_role": "nucleus"},
                                                  "cell": {"enabled": True, "channel_role": "cell_boundary"}}}}
        with self.assertRaisesRegex(ValueError, "no fields"):
            PILOT.select_fields([{"well": "A01", "site": "s0", "timepoint": 0, "z": 0, "channel": 0}], config, 1)
