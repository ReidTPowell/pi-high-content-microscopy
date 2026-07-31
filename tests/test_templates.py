from __future__ import annotations

import importlib.util
import json
import tempfile
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


CONTRACT = load("hca_contract")
TEMPLATES = load("hca_templates")
RELEASE = load("hca_release")
PILOT = load("hca_pilot_plan")


class TemplateTests(unittest.TestCase):
    def test_catalog_contains_thirteen_unique_valid_templates(self):
        catalog = TEMPLATES.catalog()
        self.assertEqual(len(catalog), 13)
        self.assertEqual(len({item["id"] for item in catalog}), 13)
        for item in catalog:
            config = json.loads(Path(item["path"]).read_text())
            self.assertEqual(CONTRACT.validate_config(config), [], item["id"])

    def test_release_review_stages_follow_the_object_graph(self):
        self.assertEqual(RELEASE.required_review_stages({"analysis": {"segmentation": {
            "nucleus": {"enabled": True}, "cell": {"enabled": False}}}}), {"nucleus", "filter"})
        self.assertEqual(RELEASE.required_review_stages({"analysis": {"segmentation": {
            "nucleus": {"enabled": False}, "cell": {"enabled": True}}}}), {"cell", "filter"})
        self.assertEqual(RELEASE.required_review_stages({"analysis": {"segmentation": {}}}), set())

    def test_cell_only_pilot_selects_the_cell_channel(self):
        config = json.loads((TEMPLATES.CONFIG_DIR / "hcsai-cell-morphology.json").read_text())
        records = [{"well": "A01", "site": f"s{index}", "timepoint": 0, "z": 0, "channel": 0,
                    "path": f"field-{index}.tif", "acquisition": {"MeanIntensity": index}}
                   for index in range(3)]
        fields = PILOT.select_fields(records, config, 3)
        self.assertTrue(all(set(field["channels"]) == {"0"} for field in fields))

    def test_installed_module_finds_shared_template_data(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            installed = prefix / "share/pi-high-content-microscopy/configs"
            installed.mkdir(parents=True)
            self.assertEqual(
                TEMPLATES.find_config_dir(prefix / "site-packages/hca_templates.py", prefix), installed
            )
