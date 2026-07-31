import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"


class ReportTests(unittest.TestCase):
    def test_aggregates_relationship_qc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "analysis"
            well = root / "wells/A01"
            well.mkdir(parents=True)
            (well / "pipeline-summary.json").write_text(json.dumps({"fields": [{"relationship_qc": "failed",
                "relationship": {"nuclei": 10, "cells": 9, "assigned": 8, "orphan": 2}}]}))
            output = Path(temp) / "report"
            result = subprocess.run([sys.executable, str(ROOT / "hca_report.py"), "--analysis-root", str(root),
                                     "--output-dir", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["relationship_qc_failed"], 1)
            self.assertEqual(report["assigned"], 8)
