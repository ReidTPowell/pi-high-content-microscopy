import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_heldout", ROOT / "hca_heldout.py")
HELDOUT = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(HELDOUT)


class HeldoutTests(unittest.TestCase):
    def test_selects_untouched_wells_and_satisfies_field_minimum(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); jobs = []
            for index, well in enumerate(("A01", "B01", "C01", "D01")):
                manifest = root / f"{well}.jsonl"
                records = [{"well": well, "site": f"s{site}", "timepoint": 0, "z": 0}
                           for site in range(index + 1)]
                manifest.write_text("\n".join(json.dumps(item) for item in records) + "\n")
                jobs.append({"well": well, "manifest": str(manifest)})
            selected = HELDOUT.choose_jobs({"jobs": jobs}, "A01", minimum_wells=2, minimum_fields=5)
            self.assertNotIn("A01", [job["well"] for job in selected])
            self.assertGreaterEqual(sum(len(HELDOUT.field_ids(Path(job["manifest"]))) for job in selected), 5)

    def test_automated_review_requires_named_human_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); overlay = root / "overlay.tif"; overlay.write_bytes(b"x")
            (root / "heldout-evidence.json").write_text(json.dumps({"wells": ["B01"], "fields": [
                {"id": "B01-s1", "well": "B01", "overlay": str(overlay),
                 "overlay_sha256": HELDOUT.sha256(overlay), "relationship_qc": "passed"}]}))
            proposal = HELDOUT.submit_vision_review(root, "vision-model", [
                {"id": "B01-s1", "decision": "accepted"}])
            self.assertEqual(proposal["status"], "human_approval_required")
            validation = HELDOUT.approve_vision_review(proposal, "human-reviewer")
            self.assertEqual(validation["status"], "passed")
            self.assertEqual(validation["reviewer"], "human-reviewer")
