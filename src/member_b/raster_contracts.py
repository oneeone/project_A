"""Validation for the frozen Member A to Member B raster contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RasterSignature:
    crs: str
    transform: tuple[float, ...]
    width: int
    height: int

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "RasterSignature":
        transform = tuple(float(value) for value in profile["transform"])
        return cls(
            crs=str(profile["crs"]),
            transform=transform,
            width=int(profile["width"]),
            height=int(profile["height"]),
        )


def compare_signatures(
    reference: RasterSignature,
    candidate: RasterSignature,
    *,
    transform_tolerance: float = 1e-9,
) -> list[str]:
    problems: list[str] = []
    if reference.crs != candidate.crs:
        problems.append(f"CRS mismatch: {candidate.crs} != {reference.crs}")
    if reference.width != candidate.width or reference.height != candidate.height:
        problems.append(
            "shape mismatch: "
            f"{candidate.width}x{candidate.height} != {reference.width}x{reference.height}"
        )
    if len(reference.transform) != len(candidate.transform) or not np.allclose(
        reference.transform,
        candidate.transform,
        rtol=0.0,
        atol=transform_tolerance,
    ):
        problems.append("transform mismatch")
    return problems


def validate_flood_arrays(
    probability: np.ndarray,
    mask: np.ndarray,
    uncertainty: np.ndarray,
    *,
    float_nodata: float = -9999.0,
    mask_nodata: int = 255,
) -> list[str]:
    problems: list[str] = []
    if probability.shape != mask.shape or probability.shape != uncertainty.shape:
        problems.append("probability, mask and uncertainty shapes differ")
        return problems
    valid_probability = probability != float_nodata
    if np.any(~np.isfinite(probability[valid_probability])):
        problems.append("probability contains non-finite values")
    if np.any(
        (probability[valid_probability] < 0) | (probability[valid_probability] > 1)
    ):
        problems.append("probability values fall outside 0..1")
    mask_values = set(np.unique(mask).tolist())
    if not mask_values.issubset({0, 1, mask_nodata}):
        problems.append(f"mask values violate 0/1/{mask_nodata}: {sorted(mask_values)}")
    valid_uncertainty = uncertainty != float_nodata
    if np.any(~np.isfinite(uncertainty[valid_uncertainty])):
        problems.append("uncertainty contains non-finite values")
    if np.any(uncertainty[valid_uncertainty] < 0):
        problems.append("uncertainty contains negative values")
    valid_mask = mask != mask_nodata
    if not np.array_equal(valid_probability, valid_uncertainty):
        problems.append("probability and uncertainty NoData footprints differ")
    if not np.array_equal(valid_probability, valid_mask):
        problems.append("floating rasters and mask NoData footprints differ")
    return problems


def validate_flood_rasters(
    reference_path: str | Path,
    probability_path: str | Path,
    mask_path: str | Path,
    uncertainty_path: str | Path,
) -> list[str]:
    """Open GeoTIFFs with rasterio and validate signatures, types and values."""

    try:
        import rasterio
    except ImportError as exc:
        raise RuntimeError("rasterio is required to validate GeoTIFF files") from exc

    problems: list[str] = []
    with (
        rasterio.open(reference_path) as reference,
        rasterio.open(probability_path) as probability,
        rasterio.open(mask_path) as mask,
        rasterio.open(uncertainty_path) as uncertainty,
    ):
        reference_signature = RasterSignature.from_profile(reference.profile)
        for name, dataset in (
            ("probability", probability),
            ("mask", mask),
            ("uncertainty", uncertainty),
        ):
            signature = RasterSignature.from_profile(dataset.profile)
            problems.extend(
                f"{name}: {problem}"
                for problem in compare_signatures(reference_signature, signature)
            )
        if probability.dtypes[0] != "float32":
            problems.append("probability dtype must be float32")
        if mask.dtypes[0] != "uint8":
            problems.append("mask dtype must be uint8")
        if uncertainty.dtypes[0] not in {"float32", "float64"}:
            problems.append("uncertainty dtype must be floating point")
        problems.extend(
            validate_flood_arrays(
                probability.read(1),
                mask.read(1),
                uncertainty.read(1),
            )
        )
    return problems
