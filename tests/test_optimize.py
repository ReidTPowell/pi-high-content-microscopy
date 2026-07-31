import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_optimize", ROOT / "hca_optimize.py")
OPTIMIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPTIMIZE)


class OptimizeTests(unittest.TestCase):
    def test_automated_review_stops_for_human_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            candidates = Path(temp) / "candidates.json"
            candidates.write_text("{}")
            review = Path(temp) / "review.json"
            review.write_text(json.dumps({"selected_candidate": {"id": "candidate-01", "score": 94,
                "objective_score": 92, "acceptable": True, "parameters": {}}}))
            state = OPTIMIZE.initialize("automated", candidates, 3, 90)
            result = OPTIMIZE.advance(state, review)
            self.assertEqual(result["status"], "human_approval_required")

    def test_defect_review_proposes_bounded_next_sweep(self):
        with tempfile.TemporaryDirectory() as temp:
            candidates = Path(temp) / "candidates.json"
            candidates.write_text("{}")
            review = Path(temp) / "review.json"
            review.write_text(json.dumps({"selected_candidate": "candidate-01", "candidate_reviews": [{
                "id": "candidate-01", "score": 70, "acceptable": True, "issues": ["false_positives"],
                "parameters": {"diameter": 20, "flow_threshold": 0.4, "cellprob_threshold": 0}}]}))
            state = OPTIMIZE.initialize("automated", candidates, 3, 90)
            result = OPTIMIZE.advance(state, review)
            self.assertEqual(result["status"], "next_sweep_required")
            self.assertEqual(result["round"], 2)
            self.assertIn(0.5, result["next_sweep"]["cellprob_thresholds"])

    def test_rejected_reference_can_drive_next_round(self):
        with tempfile.TemporaryDirectory() as temp:
            candidates = Path(temp) / "candidates.json"
            candidates.write_text("{}")
            review = Path(temp) / "review.json"
            review.write_text(json.dumps({"reference_candidate": {"id": "candidate-01", "score": 55,
                "objective_score": 55, "acceptable": False, "issues": ["false_positives"],
                "parameters": {"diameter": None, "flow_threshold": 0.4, "cellprob_threshold": 0}}}))
            result = OPTIMIZE.advance(OPTIMIZE.initialize("automated", candidates, 3, 90), review)
            self.assertEqual(result["status"], "next_sweep_required")
