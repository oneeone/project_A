"""Area definitions and uncertainty-aware flood summaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class WaterChangeSummary:
    """Area statistics required by Problem A, Question 3."""

    study_area_km2: float
    permanent_water_area_km2: float | None
    new_inundation_area_km2: float
    receded_water_area_km2: float
    net_increase_area_km2: float
    regional_new_inundation_ratio: float
    non_permanent_land_new_inundation_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProbabilisticArea:
    """Expected flooded area and a clipped normal-approximation interval."""

    expected_area_km2: float
    lower_area_km2: float
    upper_area_km2: float
    confidence_level: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def _pixel_area_array(pixel_area_m2: float | np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    area = np.asarray(pixel_area_m2, dtype=np.float64)
    if area.ndim == 0:
        if float(area) <= 0:
            raise ValueError("pixel_area_m2 must be positive")
        return np.full(shape, float(area), dtype=np.float64)
    if area.shape != shape:
        raise ValueError(f"pixel_area_m2 shape {area.shape} does not match {shape}")
    if np.any(~np.isfinite(area)) or np.any(area <= 0):
        raise ValueError("pixel_area_m2 must contain positive finite values")
    return area


def _binary_water(values: np.ndarray, nodata: int | float | None) -> tuple[np.ndarray, np.ndarray]:
    array = np.asarray(values)
    if array.ndim != 2:
        raise ValueError("water masks must be two-dimensional")
    valid = np.ones(array.shape, dtype=bool)
    if nodata is not None:
        valid &= array != nodata
    valid &= np.isfinite(array)
    unique = set(np.unique(array[valid]).tolist())
    if not unique.issubset({0, 1, False, True}):
        raise ValueError(f"water mask contains values outside 0/1: {sorted(unique)}")
    return array == 1, valid


def summarize_water_change(
    before_water: np.ndarray,
    during_water: np.ndarray,
    pixel_area_m2: float | np.ndarray,
    *,
    study_mask: np.ndarray | None = None,
    permanent_water: np.ndarray | None = None,
    nodata: int | float | None = 255,
) -> WaterChangeSummary:
    """Calculate new, receded and net water areas on an equal-area grid.

    ``before_water`` and ``during_water`` are binary water masks. The optional
    permanent-water mask is used only for the effective-land denominator.
    """

    before, before_valid = _binary_water(before_water, nodata)
    during, during_valid = _binary_water(during_water, nodata)
    if before.shape != during.shape:
        raise ValueError("before_water and during_water must have identical shapes")

    valid = before_valid & during_valid
    if study_mask is not None:
        study = np.asarray(study_mask, dtype=bool)
        if study.shape != before.shape:
            raise ValueError("study_mask must match the water masks")
        valid &= study

    areas = _pixel_area_array(pixel_area_m2, before.shape)
    study_area_m2 = float(areas[valid].sum())
    if study_area_m2 <= 0:
        raise ValueError("no valid study-area pixels")

    new_water = during & ~before & valid
    receded = before & ~during & valid
    new_area_m2 = float(areas[new_water].sum())
    receded_area_m2 = float(areas[receded].sum())

    permanent_area_m2: float | None = None
    land_ratio: float | None = None
    if permanent_water is not None:
        permanent, permanent_valid = _binary_water(permanent_water, nodata)
        if permanent.shape != before.shape:
            raise ValueError("permanent_water must match the water masks")
        permanent_pixels = permanent & valid & permanent_valid
        permanent_area_m2 = float(areas[permanent_pixels].sum())
        land_area_m2 = study_area_m2 - permanent_area_m2
        land_ratio = new_area_m2 / land_area_m2 if land_area_m2 > 0 else None

    return WaterChangeSummary(
        study_area_km2=study_area_m2 / 1_000_000.0,
        permanent_water_area_km2=(
            None if permanent_area_m2 is None else permanent_area_m2 / 1_000_000.0
        ),
        new_inundation_area_km2=new_area_m2 / 1_000_000.0,
        receded_water_area_km2=receded_area_m2 / 1_000_000.0,
        net_increase_area_km2=(new_area_m2 - receded_area_m2) / 1_000_000.0,
        regional_new_inundation_ratio=new_area_m2 / study_area_m2,
        non_permanent_land_new_inundation_ratio=land_ratio,
    )


def summarize_new_inundation_mask(
    flood_mask: np.ndarray,
    pixel_area_m2: float | np.ndarray,
    *,
    study_mask: np.ndarray | None = None,
    permanent_water: np.ndarray | None = None,
    nodata: int | float | None = 255,
) -> dict[str, float | None]:
    """Summarize Member A's new-inundation mask without inventing recession."""

    flooded, valid = _binary_water(flood_mask, nodata)
    if study_mask is not None:
        supplied = np.asarray(study_mask, dtype=bool)
        if supplied.shape != flooded.shape:
            raise ValueError("study_mask must match flood_mask")
        valid &= supplied
    areas = _pixel_area_array(pixel_area_m2, flooded.shape)
    study_area_m2 = float(areas[valid].sum())
    if study_area_m2 <= 0:
        raise ValueError("no valid study-area pixels")
    flood_area_m2 = float(areas[flooded & valid].sum())

    permanent_area_m2: float | None = None
    land_ratio: float | None = None
    if permanent_water is not None:
        permanent, permanent_valid = _binary_water(permanent_water, nodata)
        if permanent.shape != flooded.shape:
            raise ValueError("permanent_water must match flood_mask")
        permanent_area_m2 = float(areas[permanent & permanent_valid & valid].sum())
        land_area_m2 = study_area_m2 - permanent_area_m2
        land_ratio = flood_area_m2 / land_area_m2 if land_area_m2 > 0 else None

    return {
        "study_area_km2": study_area_m2 / 1_000_000.0,
        "permanent_water_area_km2": (
            None if permanent_area_m2 is None else permanent_area_m2 / 1_000_000.0
        ),
        "new_inundation_area_km2": flood_area_m2 / 1_000_000.0,
        "regional_new_inundation_ratio": flood_area_m2 / study_area_m2,
        "non_permanent_land_new_inundation_ratio": land_ratio,
        "receded_water_area_km2": None,
        "net_increase_area_km2": None,
    }


def probabilistic_flood_area(
    probability: np.ndarray,
    pixel_area_m2: float | np.ndarray,
    *,
    uncertainty: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
    confidence_level: float = 0.95,
) -> ProbabilisticArea:
    """Propagate pixel probabilities and optional standard deviations to area.

    Pixel predictions are treated as independent for this diagnostic interval.
    The assumption is intentionally explicit because spatial correlation makes
    the interval optimistic; Monte Carlo spatial ensembles should replace it
    when Member A provides per-model predictions.
    """

    probabilities = np.asarray(probability, dtype=np.float64)
    if probabilities.ndim != 2:
        raise ValueError("probability must be two-dimensional")
    valid = np.isfinite(probabilities) & (probabilities >= 0) & (probabilities <= 1)
    if valid_mask is not None:
        mask = np.asarray(valid_mask, dtype=bool)
        if mask.shape != probabilities.shape:
            raise ValueError("valid_mask must match probability")
        valid &= mask
    if not np.any(valid):
        raise ValueError("no valid probability pixels")

    areas = _pixel_area_array(pixel_area_m2, probabilities.shape)
    expected_m2 = float(np.sum(probabilities[valid] * areas[valid]))

    bernoulli_variance = probabilities * (1.0 - probabilities)
    variance = float(np.sum(bernoulli_variance[valid] * np.square(areas[valid])))
    if uncertainty is not None:
        sigma = np.asarray(uncertainty, dtype=np.float64)
        if sigma.shape != probabilities.shape:
            raise ValueError("uncertainty must match probability")
        if np.any(sigma[valid] < 0) or np.any(~np.isfinite(sigma[valid])):
            raise ValueError("uncertainty must be finite and nonnegative")
        variance += float(np.sum(np.square(sigma[valid] * areas[valid])))

    if confidence_level != 0.95:
        raise ValueError("only the 0.95 normal interval is currently supported")
    z_value = 1.959963984540054
    half_width_m2 = z_value * np.sqrt(max(variance, 0.0))
    max_area_m2 = float(areas[valid].sum())
    lower_m2 = max(0.0, expected_m2 - half_width_m2)
    upper_m2 = min(max_area_m2, expected_m2 + half_width_m2)
    return ProbabilisticArea(
        expected_area_km2=expected_m2 / 1_000_000.0,
        lower_area_km2=lower_m2 / 1_000_000.0,
        upper_area_km2=upper_m2 / 1_000_000.0,
        confidence_level=confidence_level,
    )
