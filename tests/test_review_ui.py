import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_review_ui", ROOT / "hca_review_ui.py")
REVIEW_UI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW_UI)


class ReviewUiTests(unittest.TestCase):
    def test_false_positive_feedback_tightens_cell_probability(self):
        result = REVIEW_UI.recommendations({"review_status": "revise", "selected_candidate": "candidate-01",
            "candidate_reviews": [{"id": "candidate-01", "parameters": {"diameter": 20,
                "flow_threshold": 0.4, "cellprob_threshold": 0}, "issues": ["false_positives"]}]})
        self.assertEqual(result["status"], "refinement_required")
        self.assertIn(0.5, result["next_sweep"]["cellprob_thresholds"])

    def test_build_escapes_candidate_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidates = root / "candidates.json"
            candidates.write_text(json.dumps({"image": str(root / "raw.tif"), "candidates": [{"id": "<candidate>",
                "returncode": 0, "overlay": str(root / "overlay.tif"), "parameters": {}, "object_count": 1}]}))
            original = REVIEW_UI.image_to_png
            REVIEW_UI.image_to_png = lambda source, destination: (destination.parent.mkdir(parents=True, exist_ok=True), destination.write_bytes(b"png"))
            try:
                REVIEW_UI.build(candidates, root / "review")
            finally:
                REVIEW_UI.image_to_png = original
            document = (root / "review/index.html").read_text()
            self.assertIn("&lt;candidate&gt;", document)
            self.assertNotIn("data-id=\"<candidate>\"", document)

    def test_rejects_inverted_filter_range(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            REVIEW_UI.validate_review({"reviewer": "operator", "review_status": "approved",
                "selected_candidate": "candidate-01", "candidate_reviews": [{"id": "candidate-01", "score": 90, "acceptable": True}],
                "filter_recommendations": {"nucleus": {"min_area_px": 100, "max_area_px": 50}}})
