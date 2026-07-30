import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_runner", ROOT / "hca_runner.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RunnerTests(unittest.TestCase):
    def test_success_promotes_staging_to_atomic_final_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "results"
            job = {"well": "A01", "manifest": "input.jsonl"}
            result = RUNNER.run_job(
                job, "python3 -c \"open('result.txt', 'w').write('{well}')\"", output, retries=0, gpus=[]
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual((output / "A01" / "result.txt").read_text(), "A01")
            self.assertTrue((output / "A01" / "complete.json").exists())
            self.assertFalse((output / ".A01.staging").exists())

    def test_existing_staging_fails_without_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            staging = output / ".A01.staging"
            staging.mkdir()
            (staging / "preserve.txt").write_text("keep")
            result = RUNNER.run_job({"well": "A01", "manifest": "x"}, "true", output, 0, [])
            self.assertEqual(result["status"], "failed")
            self.assertEqual((staging / "preserve.txt").read_text(), "keep")
