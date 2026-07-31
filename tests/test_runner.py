from __future__ import annotations

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
    def pipeline(self, root: Path, failure: str | None = None) -> Path:
        script = root / "pipeline.py"
        body = """import argparse\nfrom pathlib import Path\np=argparse.ArgumentParser()\np.add_argument('--well-manifest'); p.add_argument('--config'); p.add_argument('--source-root'); p.add_argument('--output-dir'); p.add_argument('--fail-on-qc',action='store_true')\na=p.parse_args()\nPath(a.output_dir,'result.txt').write_text(Path(a.well_manifest).stem)\n"""
        if failure:
            body += f"raise SystemExit({failure!r})\n"
        script.write_text(body)
        return script

    def release(self, root: Path) -> dict:
        config = root / "config.json"
        config.write_text("{}")
        return {"id": "release-test", "source_root": str(root), "config": {"path": str(config)}}

    def test_success_promotes_staging_to_atomic_final_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "results"
            manifest = root / "A01.jsonl"; manifest.write_text("{}\n")
            result = RUNNER.run_job({"well": "A01", "manifest": str(manifest)}, self.release(root),
                output, retries=0, gpus=[], job_index=0, pipeline_script=self.pipeline(root))
            self.assertEqual(result["status"], "complete")
            self.assertEqual((output / "A01" / "result.txt").read_text(), "A01")
            self.assertTrue((output / "A01" / "complete.json").exists())
            self.assertFalse((output / ".A01.staging").exists())

    def test_existing_staging_fails_without_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root / "results"
            staging = output / ".A01.staging"; staging.mkdir(parents=True)
            (staging / "preserve.txt").write_text("keep")
            result = RUNNER.run_job({"well": "A01", "manifest": str(root / "x")}, self.release(root),
                output, 0, [], 0, self.pipeline(root))
            self.assertEqual(result["failure_class"], "startup")
            self.assertEqual((staging / "preserve.txt").read_text(), "keep")

    def test_gpu_assignment_uses_stable_job_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); manifest = root / "B01.jsonl"; manifest.write_text("{}\n")
            result = RUNNER.run_job({"well": "B01", "manifest": str(manifest)}, self.release(root),
                root / "results", 0, [2, 7], 3, self.pipeline(root))
            self.assertEqual(result["gpu"], 7)

    def test_startup_failure_is_not_retried(self):
        category, signature = RUNNER.classify_failure(1, "", "ModuleNotFoundError: no module named x")
        self.assertEqual(category, "startup")
        self.assertTrue(signature.startswith("startup:"))

    def test_pipeline_invocation_is_an_argument_vector(self):
        command = RUNNER.pipeline_invocation({"manifest": "/tmp/a file.jsonl"},
            {"config": {"path": "/tmp/config.json"}, "source_root": "/tmp/source"},
            Path("/tmp/output"), Path("/tmp/pipeline.py"), False)
        self.assertIsInstance(command, list)
        self.assertIn("/tmp/a file.jsonl", command)
        self.assertNotIn("shell=True", " ".join(command))

    def test_repeated_startup_failures_stop_new_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); manifest = root / "manifest.jsonl"; manifest.write_text("manifest")
            release_path = root / "release.json"; release_path.write_text("{}")
            release = self.release(root)
            release.update({"_path": str(release_path), "manifest": {"sha256": RUNNER.sha256(manifest)}})
            jobs = []
            for index in range(8):
                path = root / f"W{index}.jsonl"; path.write_text("{}\n")
                jobs.append({"well": f"W{index}", "manifest": str(path)})
            results, aborted = RUNNER.execute({"source_manifest": str(manifest), "jobs": jobs}, release,
                root / "run", workers=2, retries=2, gpus=[], fail_fast_count=2,
                pipeline_script=self.pipeline(root, "ModuleNotFoundError: missing dependency"))
            self.assertTrue(aborted)
            self.assertLessEqual(sum(item["status"] == "failed" for item in results), 3)
            self.assertGreaterEqual(sum(item["status"] == "not_started" for item in results), 5)
            self.assertTrue((root / "run/journal.jsonl").is_file())
