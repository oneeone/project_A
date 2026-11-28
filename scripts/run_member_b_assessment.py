"""Validate Member A rasters and generate Member B's assessment handoff."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.member_b.area import probabilistic_flood_area, summarize_new_inundation_mask
from src.member_b.assessment import aggregate_assessment_grid
from src.member_b.outputs import write_assessment_outputs, write_json
from src.member_b.raster_contracts import (
    RasterSignature,
    compare_signatures,
    validate_flood_rasters,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--study-mask", type=Path, required=True)
    parser.add_argument("--assessment-grid", type=Path, required=True)
    parser.add_argument("--probability", type=Path, required=True)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--uncertainty", type=Path, required=True)
    parser.add_argument("--permanent-water", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--status", choices=("mock", "candidate", "final"), required=True)
    parser.add_argument("--version", default="v01")
    arguments = parser.parse_args()

    problems = validate_flood_rasters(
        arguments.reference,
        arguments.probability,
        arguments.mask,
        arguments.uncertainty,
    )
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}")
        return 1

    try:
        import geopandas as gpd
        import numpy as np
        import rasterio
    except ImportError as exc:
        raise RuntimeError(
            "geopandas, numpy and rasterio are required for the assessment pipeline"
        ) from exc

    frozen_grid = gpd.read_file(arguments.assessment_grid)
    if frozen_grid.empty:
        raise ValueError("assessment grid contains no cells")
    required_grid_fields = {
        "grid_id",
        "sequence",
        "center_x",
        "center_y",
        "grid_area_km2",
        "row_start",
        "row_stop",
        "column_start",
        "column_stop",
    }
    missing_grid_fields = required_grid_fields - set(frozen_grid.columns)
    if missing_grid_fields:
        raise ValueError(
            f"assessment grid is missing fields: {sorted(missing_grid_fields)}"
        )
    frozen_grid = frozen_grid.sort_values("sequence").reset_index(drop=True)
    expected_sequence = list(range(1, len(frozen_grid) + 1))
    if frozen_grid["sequence"].tolist() != expected_sequence:
        raise ValueError("assessment grid sequence must be continuous from 1")
    expected_ids = [
        f"G{index:06d}" for index in range(1, len(frozen_grid) + 1)
    ]
    if frozen_grid["grid_id"].astype(str).tolist() != expected_ids:
        raise ValueError(
            "assessment grid IDs must be continuous G000001 identifiers"
        )
    if frozen_grid.geometry.isna().any() or frozen_grid.geometry.is_empty.any():
        raise ValueError("assessment grid contains missing or empty geometry")
    if (frozen_grid["grid_area_km2"].astype(float) <= 0).any():
        raise ValueError("assessment grid contains non-positive cell area")
    grid_cells = frozen_grid.drop(columns="geometry").to_dict("records")
    geometry_by_grid_id = dict(
        zip(
            frozen_grid["grid_id"].astype(str),
            frozen_grid.geometry,
            strict=True,
        )
    )

    with (
        rasterio.open(arguments.reference) as reference,
        rasterio.open(arguments.study_mask) as study_source,
        rasterio.open(arguments.probability) as probability_source,
        rasterio.open(arguments.mask) as mask_source,
        rasterio.open(arguments.uncertainty) as uncertainty_source,
    ):
        signature_problems = compare_signatures(
            RasterSignature.from_profile(reference.profile),
            RasterSignature.from_profile(study_source.profile),
        )
        if signature_problems:
            raise ValueError(
                "study mask differs from reference: "
                + "; ".join(signature_problems)
            )
        if study_source.dtypes[0] != "uint8":
            raise ValueError("study mask dtype must be uint8")
        if study_source.nodata != 255:
            raise ValueError("study mask NoData must be 255")
        study_values = study_source.read(1)
        unexpected_study_values = set(np.unique(study_values)) - {0, 1, 255}
        if unexpected_study_values:
            raise ValueError(
                f"study mask contains unexpected values: "
                f"{sorted(unexpected_study_values)}"
            )
        reference_mask = study_values == 1
        if not reference_mask.any():
            raise ValueError("study mask contains no Hengzhou pixels")
        if frozen_grid.crs is None or str(frozen_grid.crs) != str(reference.crs):
            raise ValueError(
                f"assessment grid CRS {frozen_grid.crs} differs from "
                f"reference {reference.crs}"
            )
        probability = probability_source.read(1)
        mask = mask_source.read(1)
        uncertainty = uncertainty_source.read(1)
        permanent_water = None
        if arguments.permanent_water:
            with rasterio.open(arguments.permanent_water) as water_source:
                water_signature_problems = compare_signatures(
                    RasterSignature.from_profile(reference.profile),
                    RasterSignature.from_profile(water_source.profile),
                )
                if water_signature_problems:
                    raise ValueError(
                        "permanent water differs from reference: "
                        + "; ".join(water_signature_problems)
                    )
                if water_source.dtypes[0] != "uint8":
                    raise ValueError("permanent water dtype must be uint8")
                if water_source.nodata != 255:
                    raise ValueError("permanent water NoData must be 255")
                permanent_water = water_source.read(1)
                unexpected_water_values = set(np.unique(permanent_water)) - {
                    0,
                    1,
                    255,
                }
                if unexpected_water_values:
                    raise ValueError(
                        "permanent water contains unexpected values: "
                        f"{sorted(unexpected_water_values)}"
                    )

        pixel_area_m2 = abs(
            reference.transform.a * reference.transform.e
            - reference.transform.b * reference.transform.d
        )
        if not reference.crs or not reference.crs.is_projected:
            raise ValueError("reference grid must use a projected CRS for area calculation")
        pixel_size_m = pixel_area_m2**0.5
        transform = (
            reference.transform.a,
            reference.transform.b,
            reference.transform.c,
            reference.transform.d,
            reference.transform.e,
            reference.transform.f,
        )
        assessment = aggregate_assessment_grid(
            probability,
            mask,
            uncertainty,
            pixel_size_m=pixel_size_m,
            grid_size_m=1000.0,
            transform=transform,
            valid_mask=reference_mask,
            grid_cells=grid_cells,
            data_status=arguments.status,
        )
        if assessment.table["grid_id"].tolist() != expected_ids:
            raise RuntimeError("assessment output IDs differ from frozen grid")
        summary = summarize_new_inundation_mask(
            mask,
            pixel_area_m2,
            study_mask=reference_mask,
            permanent_water=permanent_water,
        )
        expected_area = probabilistic_flood_area(
            probability,
            pixel_area_m2,
            uncertainty=uncertainty,
            valid_mask=reference_mask & (mask != 255),
        )

        prefix = f"assessment_grid_{arguments.status}_{arguments.version}"
        output_dir = arguments.output_dir
        write_assessment_outputs(
            assessment.table,
            transform=transform,
            crs=str(reference.crs),
            csv_path=output_dir / f"{prefix}.csv",
            gpkg_path=output_dir / f"{prefix}.gpkg",
            geometry_by_grid_id=geometry_by_grid_id,
        )
        write_json(
            output_dir / f"intensity_thresholds_{arguments.status}_{arguments.version}.json",
            assessment.thresholds.to_dict(),
        )
        write_json(
            output_dir / f"flood_summary_{arguments.status}_{arguments.version}.json",
            {**summary, "probabilistic_area": expected_area.to_dict()},
        )
        write_json(
            output_dir / f"assessment_metadata_{arguments.status}_{arguments.version}.json",
            {
                "reference_grid": str(arguments.reference),
                "study_area_mask": str(arguments.study_mask),
                "frozen_assessment_grid": str(arguments.assessment_grid),
                "probability": str(arguments.probability),
                "mask": str(arguments.mask),
                "uncertainty": str(arguments.uncertainty),
                "permanent_water": (
                    None if arguments.permanent_water is None else str(arguments.permanent_water)
                ),
                "crs": str(reference.crs),
                "pixel_size_m": pixel_size_m,
                "assessment_grid_size_m": 1000.0,
                "data_status": arguments.status,
                "version": arguments.version,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
