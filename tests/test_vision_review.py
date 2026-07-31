import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_vision_review", ROOT / "hca_vision_review.py")
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


class VisionReviewTests(unittest.TestCase):
    def test_finalization_ranks_only_acceptable_candidates(self):
        payload = REVIEW.finalize({"reviewer": "vision-model", "candidate_reviews": [
            {"id": "candidate-01", "score": 95, "acceptable": True},
            {"id": "candidate-02", "score": 99, "acceptable": False},
            {"id": "candidate-03", "score": 90, "acceptable": True},
        ]})
        self.assertEqual(payload["selected_candidate"]["id"], "candidate-01")
        self.assertEqual([item["id"] for item in payload["ranked_acceptable_candidates"]], ["candidate-01", "candidate-03"])

    def test_finalization_requires_structured_scores(self):
        with self.assertRaises(ValueError):
            REVIEW.finalize({"reviewer": "vision-model", "candidate_reviews": [{"id": "candidate-01", "acceptable": True}]})

    def test_relationship_failures_penalize_secondary_candidate(self):
        payload = REVIEW.finalize({"reviewer": "vision-model", "candidate_reviews": [
            {"id": "candidate-01", "score": 95, "acceptable": True,
             "relationship": {"nuclei": 10, "orphan": 5, "ambiguous": 0}},
            {"id": "candidate-02", "score": 90, "acceptable": True,
             "relationship": {"nuclei": 10, "orphan": 0, "ambiguous": 0}},
        ]})
        self.assertEqual(payload["selected_candidate"]["id"], "candidate-02")

    def test_all_rejected_candidates_retain_a_refinement_reference(self):
        payload = REVIEW.finalize({"reviewer": "vision-model", "candidate_reviews": [
            {"id": "candidate-01", "score": 55, "acceptable": False, "issues": ["false_positives"]}
        ]})
        self.assertIsNone(payload["selected_candidate"])
        self.assertEqual(payload["status"], "refinement_required")
        self.assertEqual(payload["reference_candidate"]["id"], "candidate-01")
