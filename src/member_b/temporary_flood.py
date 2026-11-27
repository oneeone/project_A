"""Temporary SAR-change flood mask for Member B's mock workflow."""

from __future__ import annotations

import numpy as np


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    """Return the Otsu threshold for finite one-dimensional values."""

    data = np.asarray(values, dtype=np.float64)
    data = data[np.isfinite(data)]
    if data.size == 0:
        raise ValueError("Otsu threshold requires at least one finite value")
    if np.all(data == data[0]):
        return float(data[0])

    histogram, edges = np.histogram(data, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0
    probabilities = histogram.astype(np.float64) / histogram.sum()
    cumulative_weight = np.cumsum(probabilities)
    cumulative_mean = np.cumsum(probabilities * centers)
    total_mean = cumulative_mean[-1]
    denominator = cumulative_weight * (1.0 - cumulative_weight)
    between = np.zeros_like(denominator)
    usable = denominator > 0
    between[usable] = np.square(
        total_mean * cumulative_weight[usable] - cumulative_mean[usable]
    ) / denominator[usable]
    return float(centers[int(np.argmax(between))])


def sar_change_score(
    before_sar: np.ndarray,
    during_sar: np.ndarray,
    *,
    scale: str = "linear",
    epsilon: float = 1e-6,
) -> np.ndarray:
    """Calculate a change score where more negative values mean darkening."""

    before = np.asarray(before_sar, dtype=np.float64)
    during = np.asarray(during_sar, dtype=np.float64)
    if before.shape != during.shape or before.ndim != 2:
        raise ValueError("before_sar and during_sar must be matching 2D arrays")
    if scale == "linear":
        if np.any(before < 0) or np.any(during < 0):
            raise ValueError("linear SAR values must be nonnegative")
        return np.log((during + epsilon) / (before + epsilon))
    if scale == "db":
        return during - before
    raise ValueError("scale must be 'linear' or 'db'")


def temporary_flood_mask(
    before_sar: np.ndarray,
    during_sar: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    permanent_water: np.ndarray | None = None,
    scale: str = "linear",
    threshold: float | None = None,
    maximum_change_threshold: float | None = None,
    nodata_value: int = 255,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Create a conservative mock flood mask from SAR darkening.

    The result is only a development placeholder. It must not be presented as
    Member A's final model output.
    """

    score = sar_change_score(before_sar, during_sar, scale=scale)
    valid = np.isfinite(score)
    if valid_mask is not None:
        supplied = np.asarray(valid_mask, dtype=bool)
        if supplied.shape != score.shape:
            raise ValueError("valid_mask must match SAR arrays")
        valid &= supplied
    if not np.any(valid):
        raise ValueError("no valid SAR pixels")

    chosen = otsu_threshold(score[valid]) if threshold is None else float(threshold)
    if maximum_change_threshold is not None:
        chosen = min(chosen, float(maximum_change_threshold))

    flood = valid & (score <= chosen)
    if permanent_water is not None:
        water = np.asarray(permanent_water)
        if water.shape != score.shape:
            raise ValueError("permanent_water must match SAR arrays")
        flood &= water != 1

    output = np.full(score.shape, nodata_value, dtype=np.uint8)
    output[valid] = 0
    output[flood] = 1
    return output, score.astype(np.float32), chosen
