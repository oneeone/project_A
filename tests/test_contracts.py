import unittest

from src.common.contracts import (
    ASSESSMENT_REQUIRED_COLUMNS,
    DATA_STATUSES,
    FLOOD_RASTER_FILES,
)


class ContractTests(unittest.TestCase):
    def test_flood_outputs_are_complete(self) -> None:
        self.assertEqual(
            set(FLOOD_RASTER_FILES),
            {"probability", "mask", "uncertainty", "metadata"},
        )

    def test_assessment_grid_has_unique_id_field(self) -> None:
        self.assertEqual(ASSESSMENT_REQUIRED_COLUMNS[0], "grid_id")
        self.assertEqual(
            len(ASSESSMENT_REQUIRED_COLUMNS),
            len(set(ASSESSMENT_REQUIRED_COLUMNS)),
        )

    def test_result_statuses_are_fixed(self) -> None:
        self.assertEqual(DATA_STATUSES, {"mock", "candidate", "final"})


if __name__ == "__main__":
    unittest.main()
