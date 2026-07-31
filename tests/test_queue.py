import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


QUEUE = load("hca_queue")
RELEASE = load("hca_release")
RUNTIME = load("hca_runtime")


class QueueTests(unittest.TestCase):
    def prepare(self, root: Path):
        QUEUE.initialise(root)
        source = root / "source"; source.mkdir()
        manifest = root / "manifest.jsonl"; manifest.write_text("")
        plan = root / "plan.json"; plan.write_text(json.dumps({"source_manifest": str(manifest), "jobs": []}))
        config = root / "assay-001.json"
        config.write_text(json.dumps({"name": "test", "analysis": {"optimization": {
            "minimum_heldout_wells": 1, "minimum_heldout_fields": 1},
            "segmentation": {"cell": {"enabled": False}}}}))
        runtime_lock = root / "runtime-lock.json"; RUNTIME.capture(runtime_lock)
        overlay = root / "heldout-overlay.tif"; overlay.write_bytes(b"overlay")
        heldout = root / "heldout.json"; heldout.write_text(json.dumps({"status": "passed", "wells": ["B01"],
            "fields": [{"id": "B01-s0", "decision": "accepted", "overlay": str(overlay),
                        "overlay_sha256": RELEASE.sha256(overlay)}], "visual_review_complete": True}))
        reviews = []
        for stage in ("nucleus", "filter"):
            path = root / f"{stage}-review.json"
            decision = {"review_status": "approved", "reviewer": "reviewer",
                        "review_images": [{"path": f"{stage}.png", "decision": "accepted"}]}
            if stage == "filter":
                decision["filter_recommendations"] = {"nucleus": {"min_area_px": None}}
            path.write_text(json.dumps(decision))
            reviews.append({"stage": stage, "path": str(path)})
        state = {"phase": "release_approval_required", "input": str(source), "output": str(root / "analysis"),
                 "config": str(config), "runtime_lock": str(runtime_lock), "manifest": str(manifest),
                 "heldout_validation": str(heldout), "review_history": reviews}
        release_path, release = RELEASE.create_release(state, "operator", "reviewer")
        published = QUEUE.publish_release(root, release_path, "operator")
        return published, plan, release_path

    def test_publish_submit_claim_and_finish_create_auditable_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"
            published, plan, _ = self.prepare(root)
            request = QUEUE.submit(root, plan, root / "run-001", published["id"], "operator", 2, 1)
            self.assertTrue((root / "jobs" / f"{request['job_id']}.json").is_file())
            claimed = QUEUE.claim(root, "gpu-ws-01")
            self.assertEqual(claimed["release"]["id"], published["id"])
            result = QUEUE.finish(root, request["job_id"], "gpu-ws-01", 0, str(root / "run-001"))
            self.assertEqual(result["state"], "complete")
            self.assertEqual(QUEUE.rows(root, "jobs")[0]["state"], "complete")

    def test_unpublished_release_cannot_submit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"; QUEUE.initialise(root)
            plan = root / "plan.json"; plan.write_text('{"source_manifest":"missing","jobs":[]}')
            with self.assertRaisesRegex(ValueError, "unknown published release"):
                QUEUE.submit(root, plan, root / "run", "release-missing", "operator", 1, 0)

    def test_submitter_must_match_release_operator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"
            published, plan, _ = self.prepare(root)
            with self.assertRaisesRegex(ValueError, "submitter must match"):
                QUEUE.submit(root, plan, root / "run", published["id"], "different-operator", 1, 0)

    def test_cancel_and_retry_do_not_reuse_running_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"
            published, plan, _ = self.prepare(root)
            request = QUEUE.submit(root, plan, root / "run-001", published["id"], "operator", 1, 0)
            QUEUE.cancel(root, request["job_id"])
            QUEUE.retry(root, request["job_id"])
            self.assertEqual(QUEUE.rows(root, "jobs")[0]["state"], "queued")

    def test_initialise_preserves_legacy_jobs_during_v2_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "queue"; root.mkdir()
            import sqlite3
            database = sqlite3.connect(root / "queue.sqlite")
            database.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, config_id TEXT)")
            database.execute("INSERT INTO jobs VALUES ('legacy-job', 'cfg-old')")
            database.commit(); database.close()
            QUEUE.initialise(root)
            with QUEUE.connect(root) as migrated:
                self.assertEqual(migrated.execute("SELECT id FROM jobs_legacy_v1").fetchone()["id"], "legacy-job")
                columns = {row["name"] for row in migrated.execute("PRAGMA table_info(jobs)")}
                self.assertIn("release_id", columns)
