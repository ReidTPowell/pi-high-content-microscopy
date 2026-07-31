from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"


class DerivedMeasurementTests(unittest.TestCase):
    def test_nuclear_to_cytoplasm_ratio_uses_relationship_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nucleus = root / "nucleus.json"; cytoplasm = root / "cytoplasm.json"; relationships = root / "relationships.json"
            nucleus.write_text(json.dumps({"objects": [{"object_id": 2, "area_px": 4,
                "channels": {"target": {"sum": 40}}}]}))
            cytoplasm.write_text(json.dumps({"objects": [{"object_id": 7,
                "channels": {"target": {"mean": 2}}}]}))
            relationships.write_text(json.dumps({"relationships": [{"nucleus_id": 2, "cell_id": 7,
                "status": "assigned"}]}))
            output = root / "derived.json"
            result = subprocess.run([sys.executable, str(ROOT / "hca_derive.py"),
                "--nucleus-measurements", str(nucleus), "--cytoplasm-measurements", str(cytoplasm),
                "--relationships", str(relationships), "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            ratio = json.loads(output.read_text())["cells"][0]["channels"]["target"]["nucleus_to_cytoplasm_ratio"]
            self.assertEqual(ratio, 5.0)
