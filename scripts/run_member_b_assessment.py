"""Validate Member A rasters and generate Member B's assessment handoff."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.member_b.area import probabilistic_flood_area, summarize_new_inundation_mask
from src.member_b.assessment import aggregate_assessment_grid
from src.member_b.outputs import write_assessment_outputs, write_json
from src.member_b.raster_contracts import validate_flood_rasters


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
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
        import rasterio
    except ImportError as exc:
        raise RuntimeError("rasterio is required for the assessment pipeline") from exc

    with (
        rasterio.open(arguments.reference) as reference,
        rasterio.open(arguments.probability) as probability_source,
        rasterio.open(arguments.mask) as mask_source,
        rasterio.open(arguments.uncertainty) as uncertainty_source,
    ):
        reference_mask = reference.read(1) == 1
        probability = probability_source.read(1)
        mask = mask_source.read(1)
        uncertainty = uncertainty_source.read(1)
        permanent_water = None
        if arguments.permanent_water:
            with rasterio.open(arguments.permanent_water) as water_source:
                permanent_water = water_source.read(1)

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
            data_status=arguments.status,
        )
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
