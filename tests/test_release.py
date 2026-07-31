import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_release", ROOT / "hca_release.py")
RELEASE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(RELEASE)


class ReleaseTests(unittest.TestCase):
    def test_present_mismatched_workflow_config_hash_blocks_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = root / "config.json"; config.write_text("{}")
            with self.assertRaisesRegex(ValueError, "config hash"):
                RELEASE.create_release({"phase": "release_approval_required", "config": str(config),
                                        "config_sha256": "wrong", "runtime_lock": str(config),
                                        "manifest": str(config), "heldout_validation": str(config)},
                                       "operator", "reviewer")

    def test_incomplete_image_review_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "review.json"
            path.write_text(json.dumps({"review_status": "approved", "reviewer": "r",
                "review_images": [{"path": "x", "decision": "pending"}]}))
            with self.assertRaisesRegex(ValueError, "every review image"):
                RELEASE.approved_review(path)

    def test_heldout_minimums_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root / "heldout.json"; overlay = root / "overlay.tif"; overlay.write_bytes(b"x")
            path.write_text(json.dumps({"status": "passed", "wells": ["A01"], "fields": [{"id": "s0",
                "decision": "accepted", "overlay": str(overlay), "overlay_sha256": RELEASE.sha256(overlay)}],
                                        "visual_review_complete": True}))
            config = {"analysis": {"optimization": {"minimum_heldout_wells": 3,
                                                       "minimum_heldout_fields": 9}}}
            with self.assertRaisesRegex(ValueError, "at least 3 wells"):
                RELEASE.passed_heldout(path, config)

    def test_bound_json_copies_evidence_with_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source.json"; image = root / "source-overlay.tif"
            image.write_bytes(b"overlay")
            source.write_text(json.dumps({"fields": [{"overlay": str(image)}]}))
            destination = root / "release/heldout.json"
            RELEASE.copy_bound_json(source, destination, root / "release/evidence")
            copied = json.loads(destination.read_text())["fields"][0]["overlay"]
            self.assertFalse(Path(copied).is_absolute())
            self.assertTrue((destination.parent / copied).is_file())
