from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
SPEC = importlib.util.spec_from_file_location("hca_cli", ROOT / "hca_cli.py")
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class CliTests(unittest.TestCase):
    def test_top_level_help_succeeds(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["pihca", "--help"]), redirect_stdout(output):
            self.assertEqual(CLI.main(), 0)
        self.assertIn("pihca <command>", output.getvalue())
