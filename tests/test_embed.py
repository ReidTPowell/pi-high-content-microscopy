import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"


class EmbedTests(unittest.TestCase):
    def test_records_structured_adapter_result_and_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = root / "adapter.py"
            adapter.write_text("import json\nprint(json.dumps({'model':'test','results':[{'well':'A01','embedding':[1,2]}]}))\n")
            output = root / "embedding.json"
            result = subprocess.run([sys.executable, str(ROOT / "hca_embed.py"), "--source-root", str(root),
                "--well", "A01", "--output", str(output), "--adapter-script", str(adapter),
                "--environment", sys.prefix], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text())
            self.assertEqual(payload["result_count"], 1)
            self.assertEqual(payload["result"]["model"], "test")
            self.assertEqual(Path(payload["environment_python"]).resolve(), Path(sys.executable).resolve())

    def test_rejects_silent_empty_embedding_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = root / "adapter.py"
            adapter.write_text("print('{\"results\": []}')\n")
            output = root / "embedding.json"
            result = subprocess.run([sys.executable, str(ROOT / "hca_embed.py"), "--source-root", str(root),
                "--well", "P24", "--output", str(output), "--adapter-script", str(adapter),
                "--environment", sys.prefix], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("no embedding groups", json.loads(output.read_text())["validation_error"])
