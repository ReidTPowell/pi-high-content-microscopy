import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_workflow", ROOT / "hca_workflow.py")
WORKFLOW = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(WORKFLOW)


class WorkflowTests(unittest.TestCase):
    def test_selected_parameters_are_written_to_new_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labels = root / "labels.tif"; labels.write_bytes(b"labels")
            candidates = root / "candidates.json"
            candidates.write_text(json.dumps({"candidates": [{"id": "candidate-01", "returncode": 0,
                "labels": str(labels), "parameters": {"diameter": 22, "flow_threshold": 0.3,
                                                         "cellprob_threshold": -0.5}}]}))
            config = root / "assay-001.json"
            config.write_text(json.dumps({"analysis": {"segmentation": {"nucleus": {"cellpose": {}},
                                                                          "cell": {"enabled": False}}}}))
            review = root / "review.json"
            review.write_text(json.dumps({"review_status": "approved", "reviewer": "r",
                "selected_candidate": "candidate-01", "candidate_reviews": [{"id": "candidate-01",
                "acceptable": True, "score": 95}]}))
            state_path = root / "workflow-state.json"
            state = {"phase": "nuclei_review_required", "config": str(config),
                     "nucleus_candidates": str(candidates), "review_history": []}
            WORKFLOW.accept_segmentation(state, state_path, "nucleus", review)
            updated = json.loads(Path(state["config"]).read_text())
            self.assertEqual(Path(state["config"]).name, "assay-002.json")
            self.assertEqual(updated["analysis"]["segmentation"]["nucleus"]["diameter"], 22)
            self.assertEqual(state["phase"], "filter_review_required")

    def test_nonempty_filters_require_exclusion_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = root / "assay-001.json"
            config.write_text(json.dumps({"analysis": {"segmentation": {"nucleus": {}, "cell": {}}}}))
            review = root / "review.json"
            review.write_text(json.dumps({"review_status": "approved", "reviewer": "r",
                "review_images": [{"path": "x", "decision": "accepted"}],
                "filter_recommendations": {"nucleus": {"min_area_px": 50}}}))
            state = {"phase": "filter_review_required", "config": str(config), "output": str(root),
                     "review_history": []}
            with self.assertRaisesRegex(ValueError, "exclusion evidence"):
                WORKFLOW.accept_filters(state, root / "workflow.json", review)
