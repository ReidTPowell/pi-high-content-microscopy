from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_resume", ROOT / "hca_resume.py")
RESUME = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(RESUME)
from hca_contract import sha256


class ResumeTests(unittest.TestCase):
    def state(self, root: Path) -> tuple[Path, dict]:
        source = root / "input"; source.mkdir()
        output = root / "output"; output.mkdir()
        artifacts = {}
        for name in ("config", "manifest", "well_plan", "runtime_lock"):
            path = root / f"{name}.json"; path.write_text("{}")
            artifacts[name] = str(path)
        state = {"phase": "pilot_segmentation_required", "input": str(source),
                 "output": str(output), **artifacts, "config_sha256": sha256(Path(artifacts["config"])),
                 "review_history": []}
        path = root / "workflow-state.json"; path.write_text(json.dumps(state))
        return path, state

    def test_audit_accepts_hash_bound_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path, _state = self.state(Path(temp))
            self.assertEqual(RESUME.audit(path)["status"], "resumable")

    def test_audit_quarantines_changed_config(self):
        with tempfile.TemporaryDirectory() as temp:
            path, state = self.state(Path(temp))
            Path(state["config"]).write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, "quarantined"):
                RESUME.audit(path)

