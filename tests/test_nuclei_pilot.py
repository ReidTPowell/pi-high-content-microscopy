import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_nuclei_pilot", ROOT / "hca_nuclei_pilot.py")
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


class NucleiPilotTests(unittest.TestCase):
    def test_next_pilot_is_immutable_and_versioned(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "pilots" / "pilot-001").mkdir(parents=True)
            (output / "pilots" / "notes").mkdir()
            self.assertEqual(PILOT.next_pilot(output), output / "pilots" / "pilot-002")
