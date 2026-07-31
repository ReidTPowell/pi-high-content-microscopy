from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
SPEC = importlib.util.spec_from_file_location("hca_classify", ROOT / "hca_classify.py")
CLASSIFY = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(CLASSIFY)


class ClassificationTests(unittest.TestCase):
    def test_first_matching_control_derived_rule_wins(self):
        measurements = {"objects": [
            {"object_id": 1, "channels": {"dead_marker": {"mean": 20}}},
            {"object_id": 2, "channels": {"dead_marker": {"mean": 2}}},
        ]}
        rules = [{"label": "dead", "conditions": [{"channel_role": "dead_marker", "metric": "mean",
                                                       "operator": ">=", "threshold": 10}]}]
        result = CLASSIFY.classify(measurements, rules, "live")
        self.assertEqual(result["counts"], {"dead": 1, "live": 1})
