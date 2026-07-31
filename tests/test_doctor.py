from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_doctor", ROOT / "hca_doctor.py")
DOCTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCTOR)


class DoctorTests(unittest.TestCase):
    def test_detects_global_and_project_duplicate_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "agent"; agent.mkdir()
            project = root / "project"; (project / ".pi").mkdir(parents=True)
            (agent / "settings.json").write_text(json.dumps({"packages": [
                "https://github.com/ReidTPowell/pi-high-content-microscopy"]}))
            (project / ".pi/settings.json").write_text(json.dumps({"packages": [
                "github:ReidTPowell/pi-high-content-microscopy"]}))
            with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(agent)}):
                self.assertEqual(len(DOCTOR.active_pihca_sources(project)), 2)
