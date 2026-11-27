"""Aggregation from aligned flood rasters to the frozen 1 km assessment grid."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.common.contracts import ASSESSMENT_REQUIRED_COLUMNS, DATA_STATUSES
from src.member_b.intensity import (
    IntensityThresholds,
    calculate_intensity_score,
    classify_intensity,
    fit_intensity_thresholds,
    intensity_confidence,
    robust_unit_scale,
)


@dataclass(frozen=True)
class AssessmentResult:
    table: pd.DataFrame
    thresholds: IntensityThresholds


def _center_from_transform(
    transform: tuple[float, float, float, float, float, float],
    row: float,
    column: float,
) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    x = c + a * column + b * row
    y = f + d * column + e * row
    return float(x), float(y)


def aggregate_assessment_grid(
    flood_probability: np.ndarray,
    flood_mask: np.ndarray,
    uncertainty: np.ndarray,
    *,
    pixel_size_m: float,
    grid_size_m: float = 1000.0,
    transform: tuple[float, float, float, float, float, float] | None = None,
    valid_mask: np.ndarray | None = None,
    data_status: str = "mock",
    area_weight: float = 0.5,
    ratio_weight: float = 0.5,
    threshold_method: str = "jenks",
) -> AssessmentResult:
    """Aggregate aligned pixel outputs into stable square grid cells."""

    probability = np.asarray(flood_probability, dtype=np.float64)
    mask = np.asarray(flood_mask)
    sigma = np.asarray(uncertainty, dtype=np.float64)
    if probability.ndim != 2 or mask.shape != probability.shape or sigma.shape != probability.shape:
        raise ValueError("probability, mask and uncertainty must be matching 2D arrays")
    if data_status not in DATA_STATUSES:
        raise ValueError(f"data_status must be one of {sorted(DATA_STATUSES)}")
    if pixel_size_m <= 0 or grid_size_m <= 0:
        raise ValueError("pixel_size_m and grid_size_m must be positive")
    cells_per_side = int(round(grid_size_m / pixel_size_m))
    if not np.isclose(cells_per_side * pixel_size_m, grid_size_m):
        raise ValueError("grid_size_m must be an integer multiple of pixel_size_m")

    valid = (
        np.isfinite(probability)
        & (probability >= 0)
        & (probability <= 1)
        & np.isfinite(sigma)
        & (sigma >= 0)
        & np.isin(mask, [0, 1])
    )
    if valid_mask is not None:
        supplied = np.asarray(valid_mask, dtype=bool)
        if supplied.shape != probability.shape:
            raise ValueError("valid_mask must match raster arrays")
        valid &= supplied

    rows: list[dict[str, object]] = []
    height, width = probability.shape
    pixel_area_km2 = pixel_size_m * pixel_size_m / 1_000_000.0
    for grid_row, row_start in enumerate(range(0, height, cells_per_side)):
        row_stop = min(row_start + cells_per_side, height)
        for grid_column, column_start in enumerate(range(0, width, cells_per_side)):
            column_stop = min(column_start + cells_per_side, width)
            cell_valid = valid[row_start:row_stop, column_start:column_stop]
            valid_count = int(cell_valid.sum())
            if valid_count == 0:
                continue
            cell_mask = mask[row_start:row_stop, column_start:column_stop]
            cell_probability = probability[row_start:row_stop, column_start:column_stop]
            cell_uncertainty = sigma[row_start:row_stop, column_start:column_stop]
            flooded_count = int(np.sum((cell_mask == 1) & cell_valid))
            grid_area_km2 = valid_count * pixel_area_km2
            flood_area_km2 = flooded_count * pixel_area_km2
            center_row = (row_start + row_stop) / 2.0
            center_column = (column_start + column_stop) / 2.0
            if transform is None:
                center_x = center_column * pixel_size_m
                center_y = center_row * pixel_size_m
            else:
                center_x, center_y = _center_from_transform(
                    transform, center_row, center_column
                )
            rows.append(
                {
                    "grid_id": f"G{grid_row:04d}_{grid_column:04d}",
                    "center_x": center_x,
                    "center_y": center_y,
                    "grid_area_km2": grid_area_km2,
                    "flood_area_km2": flood_area_km2,
                    "flood_ratio": flood_area_km2 / grid_area_km2,
                    "mean_flood_prob": float(cell_probability[cell_valid].mean()),
                    "mean_uncertainty": float(cell_uncertainty[cell_valid].mean()),
                    "_row_start": row_start,
                    "_row_stop": row_stop,
                    "_column_start": column_start,
                    "_column_stop": column_stop,
                }
            )

    if not rows:
        raise ValueError("assessment grid contains no valid cells")
    table = pd.DataFrame(rows)
    scores = calculate_intensity_score(
        table["flood_area_km2"].to_numpy(),
        table["flood_ratio"].to_numpy(),
        area_weight=area_weight,
        ratio_weight=ratio_weight,
    )
    thresholds = fit_intensity_thresholds(scores, method=threshold_method)
    uncertainty_scaled = robust_unit_scale(table["mean_uncertainty"].to_numpy())
    table["intensity_score"] = scores
    table["intensity_class"] = classify_intensity(scores, thresholds)
    table["intensity_confidence"] = intensity_confidence(
        scores, thresholds, normalized_uncertainty=uncertainty_scaled
    )
    table["data_status"] = data_status

    missing = set(ASSESSMENT_REQUIRED_COLUMNS) - set(table.columns)
    if missing:
        raise RuntimeError(f"assessment output is missing columns: {sorted(missing)}")
    if table["grid_id"].duplicated().any():
        raise RuntimeError("assessment grid_id values must be unique")
    return AssessmentResult(table=table, thresholds=thresholds)
