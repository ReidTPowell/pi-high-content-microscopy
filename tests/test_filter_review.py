from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_filter_review", ROOT / "hca_filter_review.py")
FILTER_REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FILTER_REVIEW)


class FilterReviewTests(unittest.TestCase):
    def test_no_filter_review_is_explicit_and_resumable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"; source.mkdir()
            image = source / "field.tif"
            labels = root / "labels.tif"
            image.write_bytes(b"image")
            labels.write_bytes(b"labels")
            config = root / "assay.json"
            config.write_text(json.dumps({"channels": {"1": {"role": "nucleus"}}, "analysis": {
                "segmentation": {"nucleus": {"channel_role": "nucleus"}}}}))
            review = root / "segmentation-review.json"
            review.write_text(json.dumps({"review_status": "approved", "reviewer": "reviewer",
                                          "filter_recommendations": {}}))
            state = root / "workflow-state.json"
            state.write_text(json.dumps({"phase": "filter_review_required", "config": str(config),
                "input": str(source), "pilot_field": {"channels": {"1": {"path": image.name}}},
                "accepted": {"nucleus": {"labels": str(labels)}}}))
            output = root / "filter-review"

            def fake_run(command):
                if "hca_filter.py" in command[1]:
                    Path(command[command.index("--output") + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(command[command.index("--output") + 1]).write_bytes(b"filtered")
                    Path(command[command.index("--audit") + 1]).write_text(json.dumps({
                        "input_object_count": 1, "output_object_count": 1}))
                else:
                    destination = Path(command[command.index("--output") + 1])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b"overlay")

            def fake_png(_source, destination):
                destination.write_bytes(b"png")

            with patch.object(FILTER_REVIEW, "run", side_effect=fake_run), \
                    patch.object(FILTER_REVIEW, "image_to_png", side_effect=fake_png):
                first = FILTER_REVIEW.build(state, review, output)
                second = FILTER_REVIEW.build(state, review, output)
            self.assertEqual(first["filter_recommendations"]["nucleus"], FILTER_REVIEW.EMPTY_FILTER)
            self.assertEqual(second["status"], "awaiting_review")
            self.assertEqual(first["filter_evidence"][0]["removed"], 0)
