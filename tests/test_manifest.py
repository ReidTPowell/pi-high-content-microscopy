import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills/high-content-microscopy/scripts/hca_manifest.py"
SPEC = importlib.util.spec_from_file_location("hca_manifest", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ManifestTests(unittest.TestCase):
    def test_hcsai_supports_arbitrary_rows_and_columns(self):
        record = MODULE.hcsai_record(Path("assay_t12_AA384_s7_w11_z3.tiff"), Path("."))
        self.assertEqual(record["well"], "AA384")
        self.assertEqual(record["channel"], 11)
        self.assertEqual(record["timepoint"], 12)

    def test_metadata_is_attached_without_hard_coding_channels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "experiment/timepoint0/assay_t0_A01_s0_w2_z0.tif"
            image.parent.mkdir(parents=True)
            image.touch()
            (root / "experiment/image_metadata_1.csv").write_text(
                "ImageFileName,ExcitationEmissionFilter\nassay_t0_A01_s0_w2_z0.tif,Cy5\n",
                encoding="utf-8",
            )
            records = MODULE.build_manifest(root)
            self.assertEqual(records[0]["acquisition"]["ExcitationEmissionFilter"], "Cy5")

    def test_unknown_tiff_is_retained_as_generic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "flatfield.tif").touch()
            record = MODULE.build_manifest(root)[0]
            self.assertEqual(record["adapter"], "generic-tiff")
            self.assertIsNone(record["well"])

    def test_metadata_is_scoped_to_the_acquisition_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "experiment/timepoint0/assay_t0_A01_s0_w0_z0.tif"
            image.parent.mkdir(parents=True)
            image.touch()
            (root / "experiment/image_metadata_1.csv").write_text(
                "ImageSubFolderPath,ImageFileName,FovUuid\ntimepoint0,assay_t0_A01_s0_w0_z0.tif,local-id\n",
                encoding="utf-8",
            )
            self.assertEqual(MODULE.build_manifest(root)[0]["acquisition"]["FovUuid"], "local-id")

    def test_discovers_separate_acquisitions_in_a_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for plate in ("plate-one", "plate-two"):
                metadata = root / plate / "experiment/image_metadata_1.csv"
                metadata.parent.mkdir(parents=True)
                metadata.write_text("ImageFileName\n", encoding="utf-8")
            self.assertEqual(MODULE.discover_plates(root), [root / "plate-one", root / "plate-two"])

    def test_routes_vendor_files_without_inventing_coordinates(self):
        record = MODULE.generic_record(Path("field.nd2"), Path("."))
        self.assertEqual(record["adapter"], "bioio-required")
        self.assertEqual(record["format"], "nd2")
        self.assertIsNone(record["well"])


if __name__ == "__main__":
    unittest.main()
