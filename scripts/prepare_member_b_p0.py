"""Generate and verify Member B's P0 handoff to Member A."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.member_b.preprocess import (  # noqa: E402
    create_assessment_grid_json,
    create_empty_reference_grid,
)


OSM_RELATION_ID = 2775762
OSM_QUERY = "横州市, 广西, 中国"
OSM_USER_AGENT = "APMCM-project_A-member-B/1.0"


def fetch_osm_boundary(output_path: Path) -> dict[str, object]:
    parameters = urllib.parse.urlencode(
        {
            "q": OSM_QUERY,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "addressdetails": 1,
            "limit": 5,
        }
    )
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{parameters}",
        headers={"User-Agent": OSM_USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        candidates = json.load(response)
    match = next(
        (
            item
            for item in candidates
            if item.get("osm_type") == "relation"
            and int(item.get("osm_id", -1)) == OSM_RELATION_ID
        ),
        None,
    )
    if match is None or "geojson" not in match:
        raise RuntimeError(
            f"Nominatim did not return OSM relation {OSM_RELATION_ID}"
        )
    feature = {
        "type": "FeatureCollection",
        "name": "hengzhou_boundary_osm_relation_2775762",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": match.get("name", "横州市"),
                    "display_name": match.get("display_name"),
                    "osm_type": match["osm_type"],
                    "osm_id": match["osm_id"],
                    "licence": match.get("licence"),
                    "query_date": date.today().isoformat(),
                },
                "geometry": match["geojson"],
            }
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(feature, ensure_ascii=False, indent=2) + "\n")
    return match


def write_metadata(
    output_path: Path,
    grid_info: dict[str, object],
) -> None:
    payload = {
        "crs": grid_info["crs"],
        "resolution_m": grid_info["resolution_m"],
        "transform": grid_info["transform"],
        "width": grid_info["width"],
        "height": grid_info["height"],
        "nodata": {"float": -9999, "mask": 255},
        "band_order": [
            "after_sar_uint8",
            "before_red",
            "before_green",
            "before_blue",
            "before_nir",
            "after_sar_db",
        ],
        "input_scaling": {
            "sar_db_percentiles": ["pending", "pending"],
            "sar_uint8_formula": (
                "clip(round((dB - p1) / (p99 - p1) * 255), 0, 255)"
            ),
            "optical": {"gain": 0.0255, "offset": 0},
        },
        "source_image_dates": {
            "sar_before": "pending",
            "sar_after": "pending",
            "optical_before": "pending",
        },
        "permanent_water_threshold": "pending",
        "grid_1km": {
            "origin_m": [
                grid_info["transform"][2],
                grid_info["transform"][5],
            ],
            "px_per_cell": 100,
        },
        "provenance": {
            "sar_product_id": "pending",
            "optical_product_id": "pending",
            "orbit_direction": "pending",
            "relative_orbit": "pending",
            "polarization": "pending",
        },
        "boundary_provenance": {
            "source": "OpenStreetMap",
            "osm_type": "relation",
            "osm_id": OSM_RELATION_ID,
            "query_date": date.today().isoformat(),
            "license": "ODbL 1.0",
        },
        "data_status": "final",
        "delivery_stage": "P0_geometry",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def verify_outputs(
    reference_path: Path,
    grid_path: Path,
    metadata_path: Path,
) -> dict[str, object]:
    import numpy as np
    import rasterio

    with rasterio.open(reference_path) as reference:
        transform = [
            float(reference.transform.a),
            float(reference.transform.b),
            float(reference.transform.c),
            float(reference.transform.d),
            float(reference.transform.e),
            float(reference.transform.f),
        ]
        if reference.crs.to_string() != "EPSG:32649":
            raise ValueError(f"unexpected CRS: {reference.crs}")
        if reference.res != (10.0, 10.0):
            raise ValueError(f"unexpected resolution: {reference.res}")
        if reference.count != 1 or reference.dtypes[0] != "float32":
            raise ValueError("reference must be a single-band float32 raster")
        if reference.nodata != -9999.0:
            raise ValueError(f"unexpected reference NoData: {reference.nodata}")
        for _, window in reference.block_windows(1):
            block = reference.read(1, window=window)
            if not np.all(block == -9999.0):
                raise ValueError("reference contains a non-NoData pixel")
        width = reference.width
        height = reference.height

    grid = json.loads(grid_path.read_text(encoding="utf-8"))
    cells = grid["cells"]
    identifiers = [cell["grid_id"] for cell in cells]
    expected = [f"G{index:06d}" for index in range(1, len(cells) + 1)]
    if identifiers != expected:
        raise ValueError("grid_id values are not continuous in row-major order")
    if grid["px_per_cell"] != 100:
        raise ValueError("1 km grid must use 100 pixels per cell")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["transform"] != transform:
        raise ValueError("metadata transform differs from the reference raster")
    if metadata["width"] != width or metadata["height"] != height:
        raise ValueError("metadata dimensions differ from the reference raster")
    if metadata["band_order"][4] != "before_nir":
        raise ValueError("the fifth band must be before_nir (Sentinel-2 B08)")
    if metadata["band_order"][5] != "after_sar_db":
        raise ValueError("the sixth band must preserve original SAR dB")

    return {
        "reference": {
            "crs": "EPSG:32649",
            "resolution_m": 10,
            "transform": transform,
            "width": width,
            "height": height,
            "nodata": -9999,
            "all_nodata": True,
        },
        "grid": {
            "cell_count": len(cells),
            "unique_grid_ids": len(set(identifiers)) == len(identifiers),
            "continuous_grid_ids": identifiers == expected,
        },
        "metadata_matches_reference": True,
        "band_contract_confirmed": True,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--boundary",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "external"
        / "hengzhou_boundary_osm_relation_2775762.geojson",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "processed"
        / "hengzhou_reference_grid_final_v01.tif",
    )
    parser.add_argument(
        "--grid-json",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "tables" / "hengzhou_grid_1km.json",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=PROJECT_ROOT
        / "outputs"
        / "tables"
        / "hengzhou_input_metadata.json",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=PROJECT_ROOT
        / "outputs"
        / "samples"
        / "hengzhou_reference_grid_final_v01.zip",
    )
    parser.add_argument(
        "--verification",
        type=Path,
        default=PROJECT_ROOT
        / "outputs"
        / "tables"
        / "hengzhou_p0_verification_final_v01.json",
    )
    parser.add_argument(
        "--refresh-boundary",
        action="store_true",
        help="Fetch OSM relation 2775762 even if the local boundary exists.",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    if arguments.refresh_boundary or not arguments.boundary.exists():
        fetch_osm_boundary(arguments.boundary)
    grid_info = create_empty_reference_grid(
        arguments.boundary,
        arguments.reference,
    )
    create_assessment_grid_json(
        arguments.boundary,
        arguments.reference,
        arguments.grid_json,
    )
    write_metadata(arguments.metadata, grid_info)

    arguments.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        arguments.zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.write(arguments.reference, arcname=arguments.reference.name)

    verification = verify_outputs(
        arguments.reference,
        arguments.grid_json,
        arguments.metadata,
    )
    verification["source_boundary"] = {
        "osm_relation_id": OSM_RELATION_ID,
        "query_date": date.today().isoformat(),
        "license": "ODbL 1.0",
    }
    verification["zip_file"] = arguments.zip.name
    arguments.verification.parent.mkdir(parents=True, exist_ok=True)
    with arguments.verification.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as stream:
        stream.write(
            json.dumps(verification, ensure_ascii=False, indent=2) + "\n"
        )
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
