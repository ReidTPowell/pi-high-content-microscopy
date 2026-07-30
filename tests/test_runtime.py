import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_runtime", ROOT / "hca_runtime.py")
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class RuntimeTests(unittest.TestCase):
    def test_missing_lock_is_a_structured_verification_failure(self):
        ready, errors = RUNTIME.verify(Path("/does/not/exist/runtime.json"))
        self.assertFalse(ready)
        self.assertIn("runtime lock does not exist", errors[0])

    def test_captured_lock_verifies_in_the_same_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "runtime.json"
            RUNTIME.capture(lock)
            ready, errors = RUNTIME.verify(lock)
            self.assertTrue(ready, errors)
