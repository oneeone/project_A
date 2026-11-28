import json
import tempfile
import unittest
from pathlib import Path

from src.member_b.preprocess import (
    create_assessment_grid_json,
    create_empty_reference_grid,
)


class MemberBP0Tests(unittest.TestCase):
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
