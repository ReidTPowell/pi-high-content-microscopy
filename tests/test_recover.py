import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
SPEC = importlib.util.spec_from_file_location("hca_recover", ROOT / "hca_recover.py")
RECOVER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(RECOVER)


class RecoverTests(unittest.TestCase):
    def test_archives_staging_without_deleting_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"; staging = run / "wells/.A01.staging"
            staging.mkdir(parents=True); (staging / "error.json").write_text("evidence")
            records = RECOVER.archive(run)
            self.assertEqual(len(records), 1)
            archived = Path(records[0]["archived"])
            self.assertEqual((archived / "error.json").read_text(), "evidence")
            self.assertFalse(staging.exists())
