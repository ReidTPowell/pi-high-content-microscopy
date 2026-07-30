import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_pipeline", ROOT / "hca_pipeline.py")
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)


class PipelineTests(unittest.TestCase):
    def test_resolves_configured_channel_roles(self):
        config = {"channels": {"0": {"role": "nucleus"}, "1": {"role": "cell_boundary"}}}
        self.assertEqual(PIPELINE.channel_for_role(config, "nucleus"), 0)
        self.assertEqual(PIPELINE.channel_for_role(config, "cell_boundary"), 1)

    def test_rejects_ambiguous_roles(self):
        config = {"channels": {"0": {"role": "nucleus"}, "1": {"role": "nucleus"}}}
        with self.assertRaises(ValueError):
            PIPELINE.channel_for_role(config, "nucleus")

    def test_builds_auditable_filter_command(self):
        command = PIPELINE.filter_command(Path("/scripts"), Path("raw.tif"), Path("filtered.tif"), Path("audit.json"), Path("image.tif"),
                                          {"min_area_px": 20, "max_area_px": None, "min_intensity_mean": 100.0})
        self.assertIn("--min-area-px", command)
        self.assertIn("--min-intensity-mean", command)
        self.assertNotIn("--max-area-px", command)
        self.assertIn("--image", command)

    def test_passes_explicit_gpu_and_diameter(self):
        command = PIPELINE.append_segmentation_options(["segment"], {"gpu": True, "diameter": 24})
        self.assertEqual(command, ["segment", "--diameter", "24", "--gpu"])

    def test_passes_configured_cellpose_options(self):
        command = PIPELINE.append_segmentation_options(["segment"], {"engine": "cellpose", "cellpose": {
            "flow_threshold": 0.3, "cellprob_threshold": -1, "normalize": False, "tile_overlap": 0.2, "augment": True}})
        self.assertEqual(command, ["segment", "--flow-threshold", "0.3", "--cellprob-threshold", "-1", "--tile-overlap", "0.2", "--augment", "--no-normalize"])

    def test_pipeline_requires_explicit_overwrite_for_nonempty_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            output.mkdir()
            (output / "existing.txt").write_text("existing")
            config = Path(temp) / "config.json"
            config.write_text(json.dumps({"input": {}}))
            manifest = Path(temp) / "manifest.jsonl"
            manifest.write_text(json.dumps({"well": "A01"}) + "\n")
            result = subprocess.run([sys.executable, str(ROOT / "hca_pipeline.py"), "--well-manifest", str(manifest),
                                     "--config", str(config), "--source-root", temp, "--output-dir", str(output)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output directory is not empty", result.stderr)
