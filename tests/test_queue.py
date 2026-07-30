import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_queue", ROOT / "hca_queue.py")
QUEUE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUEUE)


class QueueTests(unittest.TestCase):
    def prepare(self, root: Path):
        QUEUE.initialise(root)
        config = root / "assay.json"
        config.write_text(json.dumps({"name": "test"}))
        review = root / "review.json"
        review.write_text(json.dumps({"review_status": "approved", "reviewer": "reviewer"}))
        published = QUEUE.publish_config(root, config, review, "operator")
        plan = root / "plan.json"
        plan.write_text(json.dumps({"source_manifest": str(root / "manifest.jsonl"), "jobs": []}))
        (root / "manifest.jsonl").write_text("")
        runtime_lock = root / "runtime-lock.json"
        runtime_lock.write_text(json.dumps({"python_version": "test", "packages": {}}))
        return published, plan, runtime_lock

    def test_publish_submit_claim_and_finish_create_auditable_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"
            published, plan, runtime_lock = self.prepare(root)
            request = QUEUE.submit(root, plan, root / "output", published["id"], runtime_lock, "operator", 2, 1)
            self.assertTrue((root / "jobs" / f"{request['job_id']}.json").is_file())
            claimed = QUEUE.claim(root, "gpu-ws-01")
            self.assertEqual(claimed["job_id"], request["job_id"])
            result = QUEUE.finish(root, request["job_id"], "gpu-ws-01", 0, str(root / "output"))
            self.assertEqual(result["state"], "complete")
            self.assertTrue((root / "results" / f"{request['job_id']}.json").is_file())
            self.assertEqual(QUEUE.rows(root, "jobs")[0]["state"], "complete")

    def test_rejected_review_cannot_publish_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"
            QUEUE.initialise(root)
            config, review = root / "config.json", root / "review.json"
            config.write_text("{}")
            review.write_text(json.dumps({"review_status": "pending"}))
            with self.assertRaises(ValueError):
                QUEUE.publish_config(root, config, review, "operator")

    def test_cancel_and_retry_do_not_reuse_running_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"
            published, plan, runtime_lock = self.prepare(root)
            request = QUEUE.submit(root, plan, root / "output", published["id"], runtime_lock, "operator", 1, 0)
            QUEUE.cancel(root, request["job_id"])
            self.assertEqual(QUEUE.rows(root, "jobs")[0]["state"], "cancelled")
            QUEUE.retry(root, request["job_id"])
            self.assertEqual(QUEUE.rows(root, "jobs")[0]["state"], "queued")
