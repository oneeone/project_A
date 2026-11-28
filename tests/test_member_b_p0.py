import json
import tempfile
import unittest
from pathlib import Path

from src.member_b.preprocess import (
    create_assessment_grid_json,
    create_empty_reference_grid,
    optical_reflectance_to_uint8,
    percentile_stretch_uint8,
    sar_gamma0_to_db,
)


class MemberBP0Tests(unittest.TestCase):
    def test_input_scaling_helpers_preserve_nodata(self) -> None:
        import numpy as np

        gamma0 = np.array([1.0, 0.01, 0.0, -9999.0], dtype=np.float32)
        db = sar_gamma0_to_db(gamma0)
        np.testing.assert_allclose(db[:2], [0.0, -20.0], atol=1e-5)
        self.assertEqual(db[2], -9999.0)
        stretched = percentile_stretch_uint8(db, -20.0, 0.0)
        np.testing.assert_allclose(stretched[:2], [255.0, 0.0])
        self.assertEqual(stretched[2], -9999.0)

        optical = optical_reflectance_to_uint8(
            np.array([10000.0, 5000.0, 0.0], dtype=np.float32)
        )
        np.testing.assert_allclose(optical[:2], [255.0, 128.0])
        self.assertEqual(optical[2], -9999.0)

    def test_empty_reference_and_continuous_one_km_grid(self) -> None:
        try:
            import rasterio
        except ImportError:
            self.skipTest("rasterio is not installed")

        boundary = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [109.0, 22.7],
                                [109.02, 22.7],
                                [109.02, 22.72],
                                [109.0, 22.72],
                                [109.0, 22.7],
                            ]
                        ],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary_path = root / "boundary.geojson"
            reference_path = root / "reference.tif"
            grid_path = root / "grid.json"
            boundary_path.write_text(json.dumps(boundary), encoding="utf-8")

            info = create_empty_reference_grid(
                boundary_path,
                reference_path,
            )
            grid = create_assessment_grid_json(
                boundary_path,
                reference_path,
                grid_path,
            )

            self.assertEqual(info["crs"], "EPSG:32649")
            self.assertEqual(info["resolution_m"], 10.0)
            with rasterio.open(reference_path) as reference:
                self.assertEqual(reference.dtypes[0], "float32")
                self.assertEqual(reference.nodata, -9999.0)
                self.assertTrue((reference.read(1) == -9999.0).all())

            identifiers = [cell["grid_id"] for cell in grid["cells"]]
            self.assertEqual(
                identifiers,
                [f"G{index:06d}" for index in range(1, len(grid["cells"]) + 1)],
            )
            self.assertTrue(all(cell["grid_area_km2"] > 0 for cell in grid["cells"]))


if __name__ == "__main__":
    unittest.main()
