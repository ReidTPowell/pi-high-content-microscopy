import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_pipeline", ROOT / "hca_pipeline.py")
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)


class PipelineTests(unittest.TestCase):
    def test_resolves_configured_channel_roles(self):
        config = {"channels": {"0": {"role": "nucleus"}, "1": {"role": "cell_boundary"}}}
        self.assertEqual(PIPELINE.channel_for_role(config, "nucleus"), 0)
        self.assertEqual(PIPELINE.channel_for_role(config, "cell_boundary"), 1)

    def test_rejects_ambiguous_roles(self):
        config = {"channels": {"0": {"role": "nucleus"}, "1": {"role": "nucleus"}}}
        with self.assertRaises(ValueError):
            PIPELINE.channel_for_role(config, "nucleus")
