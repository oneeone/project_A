import unittest

import numpy as np

from src.member_b.area import (
    probabilistic_flood_area,
    summarize_new_inundation_mask,
    summarize_water_change,
)
from src.member_b.assessment import aggregate_assessment_grid
from src.member_b.intensity import (
    classify_intensity,
    fit_intensity_thresholds,
    intensity_confidence,
)
from src.member_b.raster_contracts import (
    RasterSignature,
    compare_signatures,
    validate_flood_arrays,
)
from src.member_b.temporary_flood import temporary_flood_mask


class AreaTests(unittest.TestCase):
    def test_water_change_definitions(self) -> None:
        before = np.array([[1, 0], [1, 0]], dtype=np.uint8)
        during = np.array([[1, 1], [0, 0]], dtype=np.uint8)
        summary = summarize_water_change(
            before,
            during,
            100.0,
            permanent_water=before,
        )
        self.assertAlmostEqual(summary.new_inundation_area_km2, 0.0001)
        self.assertAlmostEqual(summary.receded_water_area_km2, 0.0001)
        self.assertAlmostEqual(summary.net_increase_area_km2, 0.0)
        self.assertAlmostEqual(summary.regional_new_inundation_ratio, 0.25)
        self.assertAlmostEqual(summary.non_permanent_land_new_inundation_ratio, 0.5)

    def test_new_inundation_does_not_invent_recession(self) -> None:
        mask = np.array([[1, 0], [255, 0]], dtype=np.uint8)
        summary = summarize_new_inundation_mask(mask, 100.0)
        self.assertAlmostEqual(summary["new_inundation_area_km2"], 0.0001)
        self.assertIsNone(summary["receded_water_area_km2"])
        self.assertIsNone(summary["net_increase_area_km2"])

    def test_probability_area_is_bounded(self) -> None:
        probability = np.full((2, 2), 0.5)
        result = probabilistic_flood_area(probability, 100.0)
        self.assertAlmostEqual(result.expected_area_km2, 0.0002)
        self.assertGreaterEqual(result.lower_area_km2, 0.0)
        self.assertLessEqual(result.upper_area_km2, 0.0004)


class TemporaryFloodTests(unittest.TestCase):
    def test_sar_darkening_is_detected_and_permanent_water_removed(self) -> None:
        before = np.full((4, 4), 10.0)
        during = before.copy()
        during[2:, :] = 2.0
        permanent = np.zeros((4, 4), dtype=np.uint8)
        permanent[2, 0] = 1
        mask, _, threshold = temporary_flood_mask(
            before,
            during,
            permanent_water=permanent,
            maximum_change_threshold=-0.1,
        )
        self.assertLess(threshold, 0)
        self.assertEqual(mask[0, 0], 0)
        self.assertEqual(mask[3, 3], 1)
        self.assertEqual(mask[2, 0], 0)


class IntensityAndGridTests(unittest.TestCase):
    def test_four_classes_and_unique_grid_ids(self) -> None:
        probability = np.array(
            [
                [0.1, 0.2, 0.7, 0.9],
                [0.1, 0.3, 0.8, 0.9],
                [0.0, 0.0, 0.4, 0.6],
                [0.0, 0.0, 0.5, 0.7],
            ]
        )
        mask = (probability >= 0.5).astype(np.uint8)
        uncertainty = np.full((4, 4), 0.1)
        grid_cells = [
            {
                "grid_id": f"G{index + 1:06d}",
                "center_x": float(column * 1000 + 500),
                "center_y": float(row * 1000 + 500),
                "grid_area_km2": 1.0,
                "row_start": row * 2,
                "row_stop": row * 2 + 2,
                "column_start": column * 2,
                "column_stop": column * 2 + 2,
            }
            for index, (row, column) in enumerate(
                [(0, 0), (0, 1), (1, 0), (1, 1)]
            )
        ]
        result = aggregate_assessment_grid(
            probability,
            mask,
            uncertainty,
            pixel_size_m=500.0,
            grid_size_m=1000.0,
            grid_cells=grid_cells,
            data_status="mock",
        )
        self.assertEqual(len(result.table), 4)
        self.assertEqual(
            result.table["grid_id"].tolist(),
            ["G000001", "G000002", "G000003", "G000004"],
        )
        self.assertFalse(result.table["grid_id"].duplicated().any())
        self.assertTrue(result.table["intensity_score"].between(0, 1).all())
        self.assertTrue(result.table["intensity_confidence"].between(0, 1).all())

        invalid_grid = [dict(cell) for cell in grid_cells]
        invalid_grid[0]["grid_id"] = "G0000_0000"
        with self.assertRaisesRegex(ValueError, "continuous G000001"):
            aggregate_assessment_grid(
                probability,
                mask,
                uncertainty,
                pixel_size_m=500.0,
                grid_size_m=1000.0,
                grid_cells=invalid_grid,
                data_status="mock",
            )

        invalid_area_grid = [dict(cell) for cell in grid_cells]
        invalid_area_grid[0]["grid_area_km2"] = 0.0
        with self.assertRaisesRegex(ValueError, "must be positive"):
            aggregate_assessment_grid(
                probability,
                mask,
                uncertainty,
                pixel_size_m=500.0,
                grid_size_m=1000.0,
                grid_cells=invalid_area_grid,
                data_status="mock",
            )

    def test_threshold_classification_order(self) -> None:
        scores = np.array([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
        thresholds = fit_intensity_thresholds(scores)
        self.assertEqual(thresholds.method, "jenks")
        labels = classify_intensity(scores, thresholds)
        self.assertEqual(labels[0], "一般")
        self.assertEqual(labels[-1], "极强")
        confidence = intensity_confidence(scores, thresholds)
        self.assertGreater(confidence[0], 0)
        self.assertGreater(confidence[-1], 0)


class ContractValidationTests(unittest.TestCase):
    def test_raster_signature_mismatch_is_reported(self) -> None:
        reference = RasterSignature("EPSG:32649", (1, 0, 0, 0, -1, 0), 10, 10)
        candidate = RasterSignature("EPSG:4326", (1, 0, 0, 0, -1, 0), 10, 9)
        problems = compare_signatures(reference, candidate)
        self.assertEqual(len(problems), 2)

    def test_array_contract(self) -> None:
        probability = np.array([[0.2, -9999.0]], dtype=np.float32)
        mask = np.array([[0, 255]], dtype=np.uint8)
        uncertainty = np.array([[0.1, -9999.0]], dtype=np.float32)
        self.assertEqual(validate_flood_arrays(probability, mask, uncertainty), [])


if __name__ == "__main__":
    unittest.main()
