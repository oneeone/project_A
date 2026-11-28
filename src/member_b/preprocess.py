"""Hengzhou boundary, reference-grid, alignment and stack construction."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable


def _require_gis() -> tuple[object, object, object]:
    try:
        import geopandas as gpd
        import rasterio
        from rasterio import features
    except ImportError as exc:
        raise RuntimeError(
            "geopandas, rasterio, shapely and pyproj are required for preprocessing"
        ) from exc
    return gpd, rasterio, features


def create_reference_grid(
    boundary_path: str | Path,
    output_path: str | Path,
    *,
    target_crs: str = "EPSG:32649",
    resolution_m: float = 10.0,
    mask_nodata: int = 255,
) -> dict[str, object]:
    """Rasterize the study boundary on a resolution-aligned projected grid."""

    gpd, rasterio, features = _require_gis()
    from rasterio.transform import from_origin

    boundary = gpd.read_file(boundary_path)
    if boundary.empty:
        raise ValueError("study boundary contains no features")
    if boundary.crs is None:
        raise ValueError("study boundary has no CRS")
    projected = boundary.to_crs(target_crs)
    geometry = projected.geometry.make_valid().union_all()
    if geometry.is_empty:
        raise ValueError("study boundary is empty after geometry repair")

    min_x, min_y, max_x, max_y = geometry.bounds
    left = math.floor(min_x / resolution_m) * resolution_m
    bottom = math.floor(min_y / resolution_m) * resolution_m
    right = math.ceil(max_x / resolution_m) * resolution_m
    top = math.ceil(max_y / resolution_m) * resolution_m
    width = int(round((right - left) / resolution_m))
    height = int(round((top - bottom) / resolution_m))
    transform = from_origin(left, top, resolution_m, resolution_m)
    mask = features.rasterize(
        [(geometry, 1)],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "uint8",
        "crs": target_crs,
        "transform": transform,
        "nodata": mask_nodata,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(output, "w", **profile) as destination:
        destination.write(mask, 1)
        destination.set_band_description(1, "study_area_mask")
    return {
        "crs": target_crs,
        "resolution_m": resolution_m,
        "width": width,
        "height": height,
        "bounds": [left, bottom, right, top],
        "mask_nodata": mask_nodata,
    }


def _projected_study_geometry(
    boundary_path: str | Path,
    target_crs: str,
) -> tuple[object, object]:
    gpd, _, _ = _require_gis()
    boundary = gpd.read_file(boundary_path)
    if boundary.empty:
        raise ValueError("study boundary contains no features")
    if boundary.crs is None:
        raise ValueError("study boundary has no CRS")
    projected = boundary.to_crs(target_crs)
    geometry = projected.geometry.make_valid().union_all()
    if geometry.is_empty:
        raise ValueError("study boundary is empty after geometry repair")
    return projected, geometry


def create_empty_reference_grid(
    boundary_path: str | Path,
    output_path: str | Path,
    *,
    target_crs: str = "EPSG:32649",
    resolution_m: float = 10.0,
    float_nodata: float = -9999.0,
) -> dict[str, object]:
    """Create the frozen, all-NoData geometry reference requested by Member A."""

    if resolution_m <= 0:
        raise ValueError("resolution_m must be positive")
    _, rasterio, _ = _require_gis()
    import numpy as np
    from rasterio.transform import from_origin

    _, geometry = _projected_study_geometry(boundary_path, target_crs)
    min_x, min_y, max_x, max_y = geometry.bounds
    left = math.floor(min_x / resolution_m) * resolution_m
    bottom = math.floor(min_y / resolution_m) * resolution_m
    right = math.ceil(max_x / resolution_m) * resolution_m
    top = math.ceil(max_y / resolution_m) * resolution_m
    width = int(round((right - left) / resolution_m))
    height = int(round((top - bottom) / resolution_m))
    transform = from_origin(left, top, resolution_m, resolution_m)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": target_crs,
        "transform": transform,
        "nodata": float_nodata,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "BIGTIFF": "IF_SAFER",
    }
    with rasterio.open(output, "w", **profile) as destination:
        for _, window in destination.block_windows(1):
            block = np.full(
                (int(window.height), int(window.width)),
                float_nodata,
                dtype=np.float32,
            )
            destination.write(block, 1, window=window)
        destination.set_band_description(1, "empty_reference_grid")
        destination.update_tags(
            data_status="final",
            purpose="geometry_only",
            study_area="Hengzhou City",
        )

    return {
        "crs": target_crs,
        "resolution_m": resolution_m,
        "transform": [
            float(transform.a),
            float(transform.b),
            float(transform.c),
            float(transform.d),
            float(transform.e),
            float(transform.f),
        ],
        "width": width,
        "height": height,
        "bounds": [left, bottom, right, top],
        "float_nodata": float_nodata,
    }


def create_assessment_grid_json(
    boundary_path: str | Path,
    reference_path: str | Path,
    output_path: str | Path,
    *,
    grid_size_m: float = 1000.0,
) -> dict[str, object]:
    """Write the frozen 1 km cell table aligned to the 10 m reference origin."""

    _, rasterio, features = _require_gis()
    import numpy as np

    with rasterio.open(reference_path) as reference:
        if reference.crs is None:
            raise ValueError("reference grid has no CRS")
        pixel_size_x = float(reference.transform.a)
        pixel_size_y = abs(float(reference.transform.e))
        if not math.isclose(pixel_size_x, pixel_size_y):
            raise ValueError("reference grid pixels must be square")
        pixels_per_cell = int(round(grid_size_m / pixel_size_x))
        if not math.isclose(pixels_per_cell * pixel_size_x, grid_size_m):
            raise ValueError("grid_size_m must be an integer multiple of pixel size")

        _, geometry = _projected_study_geometry(
            boundary_path,
            reference.crs.to_string(),
        )
        study_mask = features.rasterize(
            [(geometry, 1)],
            out_shape=(reference.height, reference.width),
            transform=reference.transform,
            fill=0,
            dtype="uint8",
            all_touched=False,
        ).astype(bool)
        transform = reference.transform
        height = reference.height
        width = reference.width
        crs = reference.crs.to_string()

    pixel_area_km2 = pixel_size_x * pixel_size_y / 1_000_000.0
    cells: list[dict[str, object]] = []
    next_id = 1
    for row_start in range(0, height, pixels_per_cell):
        row_stop = min(row_start + pixels_per_cell, height)
        for column_start in range(0, width, pixels_per_cell):
            column_stop = min(column_start + pixels_per_cell, width)
            valid_count = int(
                np.count_nonzero(
                    study_mask[row_start:row_stop, column_start:column_stop]
                )
            )
            if valid_count == 0:
                continue
            center_row = (row_start + row_stop) / 2.0
            center_column = (column_start + column_stop) / 2.0
            center_x = (
                transform.c
                + transform.a * center_column
                + transform.b * center_row
            )
            center_y = (
                transform.f
                + transform.d * center_column
                + transform.e * center_row
            )
            cells.append(
                {
                    "grid_id": f"G{next_id:06d}",
                    "center_x": round(float(center_x), 3),
                    "center_y": round(float(center_y), 3),
                    "grid_area_km2": round(valid_count * pixel_area_km2, 6),
                }
            )
            next_id += 1

    payload = {
        "crs": crs,
        "grid_size_m": grid_size_m,
        "origin_m": [float(transform.c), float(transform.f)],
        "px_per_cell": pixels_per_cell,
        "ordering": "left_to_right_then_top_to_bottom",
        "cell_count": len(cells),
        "cells": cells,
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "{",
        f'  "crs": {json.dumps(payload["crs"])},',
        f'  "grid_size_m": {json.dumps(payload["grid_size_m"])},',
        f'  "origin_m": {json.dumps(payload["origin_m"])},',
        f'  "px_per_cell": {json.dumps(payload["px_per_cell"])},',
        f'  "ordering": {json.dumps(payload["ordering"])},',
        f'  "cell_count": {json.dumps(payload["cell_count"])},',
        '  "cells": [',
    ]
    cell_lines = [
        "    "
        + json.dumps(cell, ensure_ascii=False, separators=(", ", ": "))
        + ("," if index < len(cells) - 1 else "")
        for index, cell in enumerate(cells)
    ]
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(header + cell_lines + ["  ]", "}"]) + "\n")
    return payload


def align_raster_to_reference(
    source_path: str | Path,
    reference_path: str | Path,
    output_path: str | Path,
    *,
    resampling: str = "bilinear",
    destination_nodata: float | int = -9999,
) -> None:
    """Reproject every source band to the frozen reference grid."""

    _, rasterio, _ = _require_gis()
    from rasterio.warp import Resampling, reproject

    methods = {
        "nearest": Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
    }
    if resampling not in methods:
        raise ValueError(f"unsupported resampling method: {resampling}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(reference_path) as reference, rasterio.open(source_path) as source:
        profile = reference.profile.copy()
        profile.update(
            count=source.count,
            dtype=source.dtypes[0],
            nodata=destination_nodata,
            compress="deflate",
        )
        with rasterio.open(output, "w", **profile) as destination:
            for band in range(1, source.count + 1):
                reproject(
                    source=rasterio.band(source, band),
                    destination=rasterio.band(destination, band),
                    src_transform=source.transform,
                    src_crs=source.crs,
                    src_nodata=source.nodata,
                    dst_transform=reference.transform,
                    dst_crs=reference.crs,
                    dst_nodata=destination_nodata,
                    resampling=methods[resampling],
                )
                description = source.descriptions[band - 1]
                if description:
                    destination.set_band_description(band, description)


def build_input_stack(
    aligned_rasters: Iterable[str | Path],
    reference_path: str | Path,
    output_path: str | Path,
    *,
    band_names: Iterable[str],
    float_nodata: float = -9999.0,
) -> None:
    """Stack already aligned single-band rasters in the declared order."""

    _, rasterio, _ = _require_gis()
    paths = [Path(path) for path in aligned_rasters]
    names = list(band_names)
    if len(paths) != len(names) or not paths:
        raise ValueError("aligned_rasters and band_names must have equal nonzero length")

    with rasterio.open(reference_path) as reference:
        profile = reference.profile.copy()
        profile.update(
            count=len(paths),
            dtype="float32",
            nodata=float_nodata,
            compress="deflate",
        )
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output, "w", **profile) as destination:
            for index, (path, name) in enumerate(zip(paths, names, strict=True), start=1):
                with rasterio.open(path) as source:
                    if (
                        source.crs != reference.crs
                        or source.transform != reference.transform
                        or source.width != reference.width
                        or source.height != reference.height
                    ):
                        raise ValueError(f"raster is not aligned to reference: {path}")
                    if source.count != 1:
                        raise ValueError(f"stack source must be single-band: {path}")
                    destination.write(source.read(1).astype("float32"), index)
                    destination.set_band_description(index, name)


def write_input_metadata(
    output_path: str | Path,
    *,
    reference_grid: str,
    input_stack: str,
    permanent_water: str,
    channels: list[dict[str, object]],
    target_crs: str,
    resolution_m: float,
    source_image_dates: dict[str, str],
    processing: list[str],
    data_status: str,
) -> None:
    metadata = {
        "reference_grid": reference_grid,
        "input_stack": input_stack,
        "permanent_water": permanent_water,
        "channels": channels,
        "target_crs": target_crs,
        "resolution_m": resolution_m,
        "source_image_dates": source_image_dates,
        "processing": processing,
        "data_status": data_status,
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
