from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts"
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("hca_resources", ROOT / "hca_resources.py")
RESOURCES = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(RESOURCES)


class ResourceTests(unittest.TestCase):
    def inventory(self):
        return [
            {"index": 0, "free_mib": 20000, "utilization_percent": 2},
            {"index": 1, "free_mib": 4000, "utilization_percent": 0},
            {"index": 3, "free_mib": 24000, "utilization_percent": 95},
        ]

    def test_auto_admits_only_eligible_resources(self):
        original = RESOURCES.lock_available
        RESOURCES.lock_available = lambda _gpu: True
        try:
            self.assertEqual(RESOURCES.admit_gpus("auto", inventory=self.inventory()), [0])
        finally:
            RESOURCES.lock_available = original

    def test_explicit_ineligible_gpu_fails_instead_of_silent_fallback(self):
        with self.assertRaisesRegex(ValueError, "failed admission"):
            RESOURCES.admit_gpus("1", inventory=self.inventory(), require_unlocked=False)

    def test_auto_workers_scale_from_zero_to_available_gpu_count(self):
        self.assertEqual(RESOURCES.resolve_workers(0, gpu_ids=[0, 2, 4], requires_gpu=True,
                                                   job_count=10), 3)
        with self.assertRaisesRegex(ValueError, "requires a GPU"):
            RESOURCES.resolve_workers(0, gpu_ids=[], requires_gpu=True)

