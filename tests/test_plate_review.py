from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_plate_review", ROOT / "hca_plate_review.py")
PLATE_REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLATE_REVIEW)


class PlateReviewTests(unittest.TestCase):
    def test_existing_review_assets_are_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            (run / "run-summary.json").write_text(json.dumps({"aborted": False, "results": [
                {"status": "complete"}]}))
            report = run / "report"; report.mkdir()
            figure = report / "figure.png"; figure.write_bytes(b"png")
            (report / "report.json").write_text(json.dumps({"fields": 1, "figures": [figure.name]}))
            first = PLATE_REVIEW.build(run)
            second = PLATE_REVIEW.build(run)
            self.assertEqual(first["status"], "awaiting_review")
            self.assertEqual(second["status"], "awaiting_review")
            self.assertEqual(second["images"], 1)
