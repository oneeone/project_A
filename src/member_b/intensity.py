"""Flood intensity scoring, four-level classification and uncertainty."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


INTENSITY_CLASSES = ("一般", "较强", "强", "极强")


@dataclass(frozen=True)
class IntensityThresholds:
    method: str
    breaks: tuple[float, float, float]
    classes: tuple[str, str, str, str] = INTENSITY_CLASSES

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "breaks": list(self.breaks),
            "classes": list(self.classes),
        }


def robust_unit_scale(
    values: np.ndarray,
    *,
    lower_quantile: float = 0.05,
    upper_quantile: float = 0.95,
) -> np.ndarray:
    """Scale finite values to 0..1 using robust quantile endpoints."""

    data = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(data)
    result = np.full(data.shape, np.nan, dtype=np.float64)
    if not np.any(finite):
        return result
    lower, upper = np.quantile(data[finite], [lower_quantile, upper_quantile])
    if upper <= lower:
        result[finite] = 0.0
        return result
    result[finite] = np.clip((data[finite] - lower) / (upper - lower), 0.0, 1.0)
    return result


def calculate_intensity_score(
    flood_area_km2: np.ndarray,
    flood_ratio: np.ndarray,
    *,
    area_weight: float = 0.5,
    ratio_weight: float = 0.5,
) -> np.ndarray:
    """Combine robustly normalized flood area and flood ratio."""

    if area_weight < 0 or ratio_weight < 0 or area_weight + ratio_weight <= 0:
        raise ValueError("intensity weights must be nonnegative with a positive sum")
    area = np.asarray(flood_area_km2, dtype=np.float64)
    ratio = np.asarray(flood_ratio, dtype=np.float64)
    if area.shape != ratio.shape:
        raise ValueError("flood_area_km2 and flood_ratio must have the same shape")
    if np.any((ratio[np.isfinite(ratio)] < 0) | (ratio[np.isfinite(ratio)] > 1)):
        raise ValueError("flood_ratio must be within 0..1")

    area_scaled = robust_unit_scale(area)
    ratio_scaled = robust_unit_scale(ratio)
    total = area_weight + ratio_weight
    return (area_weight * area_scaled + ratio_weight * ratio_scaled) / total


def fit_intensity_thresholds(
    scores: np.ndarray,
    *,
    method: str = "jenks",
) -> IntensityThresholds:
    """Fit three reproducible breaks for four intensity levels."""

    data = np.asarray(scores, dtype=np.float64)
    data = data[np.isfinite(data)]
    if data.size == 0:
        raise ValueError("cannot fit intensity thresholds without finite scores")
    if method == "quantile":
        breaks = np.quantile(data, [0.25, 0.50, 0.75])
    elif method == "jenks":
        breaks = _jenks_breaks(data, class_count=4)
    else:
        raise ValueError("threshold method must be 'jenks' or 'quantile'")
    breaks = np.maximum.accumulate(breaks)
    return IntensityThresholds(method=method, breaks=tuple(float(x) for x in breaks))


def _jenks_breaks(values: np.ndarray, class_count: int) -> np.ndarray:
    """Calculate internal Jenks natural-break values by dynamic programming."""

    data = np.sort(np.asarray(values, dtype=np.float64))
    if data.size < class_count:
        return np.quantile(data, np.linspace(0, 1, class_count + 1)[1:-1])

    lower = np.zeros((data.size + 1, class_count + 1), dtype=np.int64)
    variance = np.full((data.size + 1, class_count + 1), np.inf)
    lower[0, 1:] = 1
    variance[0, 1:] = 0.0
    for length in range(1, data.size + 1):
        sum_values = 0.0
        sum_squares = 0.0
        weight = 0.0
        current_variance = 0.0
        for offset in range(1, length + 1):
            lower_index = length - offset + 1
            value = data[lower_index - 1]
            weight += 1.0
            sum_values += value
            sum_squares += value * value
            current_variance = sum_squares - (sum_values * sum_values) / weight
            previous = lower_index - 1
            if previous > 0:
                for classes in range(2, class_count + 1):
                    candidate = current_variance + variance[previous, classes - 1]
                    if candidate < variance[length, classes]:
                        lower[length, classes] = lower_index
                        variance[length, classes] = candidate
        lower[length, 1] = 1
        variance[length, 1] = current_variance

    class_breaks = np.zeros(class_count + 1, dtype=np.float64)
    class_breaks[0] = data[0]
    class_breaks[-1] = data[-1]
    index = data.size
    for classes in range(class_count, 1, -1):
        split = int(lower[index, classes] - 1)
        split = min(max(split, 1), data.size - 1)
        class_breaks[classes - 1] = (data[split - 1] + data[split]) / 2.0
        index = split
    return class_breaks[1:-1]


def classify_intensity(
    scores: np.ndarray,
    thresholds: IntensityThresholds,
) -> np.ndarray:
    """Assign Chinese four-level intensity labels."""

    data = np.asarray(scores, dtype=np.float64)
    indexes = np.searchsorted(np.asarray(thresholds.breaks), data, side="left")
    labels = np.empty(data.shape, dtype=object)
    labels[:] = None
    finite = np.isfinite(data)
    classes = np.asarray(thresholds.classes, dtype=object)
    labels[finite] = classes[indexes[finite]]
    return labels


def intensity_confidence(
    scores: np.ndarray,
    thresholds: IntensityThresholds,
    *,
    normalized_uncertainty: np.ndarray | None = None,
) -> np.ndarray:
    """Estimate confidence from threshold distance and model uncertainty."""

    data = np.asarray(scores, dtype=np.float64)
    breaks = np.asarray(thresholds.breaks, dtype=np.float64)
    indexes = np.searchsorted(breaks, data, side="left")
    class_bounds = np.asarray((0.0, *thresholds.breaks, 1.0), dtype=np.float64)
    class_width = np.maximum(
        class_bounds[indexes + 1] - class_bounds[indexes],
        1e-12,
    )
    lower_distance = np.full(data.shape, np.inf, dtype=np.float64)
    upper_distance = np.full(data.shape, np.inf, dtype=np.float64)
    has_lower = indexes > 0
    has_upper = indexes < len(breaks)
    lower_distance[has_lower] = np.abs(
        data[has_lower] - breaks[indexes[has_lower] - 1]
    )
    upper_distance[has_upper] = np.abs(data[has_upper] - breaks[indexes[has_upper]])
    distance = np.minimum(lower_distance, upper_distance)
    boundary_component = np.clip(distance / (class_width / 2.0), 0.0, 1.0)

    if normalized_uncertainty is None:
        uncertainty_component = np.ones(data.shape, dtype=np.float64)
    else:
        uncertainty = np.asarray(normalized_uncertainty, dtype=np.float64)
        if uncertainty.shape != data.shape:
            raise ValueError("normalized_uncertainty must match scores")
        uncertainty_component = 1.0 - np.clip(uncertainty, 0.0, 1.0)
    confidence = boundary_component * uncertainty_component
    confidence[~np.isfinite(data)] = np.nan
    return confidence
