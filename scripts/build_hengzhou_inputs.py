"""Build the final Hengzhou six-band stack and permanent-water mask."""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import planetary_computer as pc
import rasterio
import requests
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, bounds as window_bounds, from_bounds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.member_b.preprocess import (  # noqa: E402
    optical_reflectance_to_uint8,
    percentile_stretch_uint8,
    sar_gamma0_to_db,
)


STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
FLOAT_NODATA = -9999.0
MASK_NODATA = 255
PERMANENT_WATER_THRESHOLD = 90
SAR_AFTER_DATE = "2026-07-04"
SAR_BEFORE_DATE = "2026-06-27"
SAR_ORBIT_STATE = "ascending"
SAR_RELATIVE_ORBIT = 157
OPTICAL_ACQUISITIONS = [
    "2026-06-03T03:15:41.025000+00:00",
    "2026-05-26T03:21:51.024000+00:00",
    "2026-05-14T03:15:41.025000+00:00",
    "2026-04-09T03:15:39.024000+00:00",
    "2026-01-14T03:21:11.025000+00:00",
]
OPTICAL_ASSETS = ["B04", "B03", "B02", "B08"]
OPTICAL_BAND_NAMES = [
    "before_red",
    "before_green",
    "before_blue",
    "before_nir",
]
CLOUD_SCL_CLASSES = np.array([1, 3, 8, 9, 10, 11], dtype=np.uint8)
PROCESSING_CHUNK_SIZE = 512


def _item_summary(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "datetime": item.datetime.isoformat(),
        "bbox": item.bbox,
        "platform": item.properties.get("platform"),
        "orbit_state": item.properties.get("sat:orbit_state"),
        "relative_orbit": item.properties.get("sat:relative_orbit"),
        "polarizations": item.properties.get("sar:polarizations"),
        "mgrs_tile": item.properties.get("s2:mgrs_tile"),
        "cloud_cover": item.properties.get("eo:cloud_cover"),
    }


def _select_sar_items(catalog: Client, date: str) -> list[Any]:
    candidates = list(
        catalog.search(
            collections=["sentinel-1-rtc"],
            bbox=[108.7938338, 22.4871194, 109.6181782, 23.1370098],
            datetime=f"{date}T00:00:00Z/{date}T23:59:59Z",
        ).items()
    )
    selected = [
        item
        for item in candidates
        if item.properties.get("sat:orbit_state") == SAR_ORBIT_STATE
        and item.properties.get("sat:relative_orbit") == SAR_RELATIVE_ORBIT
        and "VV" in item.properties.get("sar:polarizations", [])
    ]
    if not selected:
        raise RuntimeError(f"no matching Sentinel-1 RTC items found for {date}")
    return sorted(selected, key=lambda item: item.id)


def _select_optical_items(catalog: Client, acquisition: str) -> list[Any]:
    day = acquisition[:10]
    candidates = list(
        catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=[108.7938338, 22.4871194, 109.6181782, 23.1370098],
            datetime=f"{day}T00:00:00Z/{day}T23:59:59Z",
        ).items()
    )
    target = datetime.fromisoformat(acquisition)
    selected_by_tile: dict[str, Any] = {}
    for item in candidates:
        tile = item.properties.get("s2:mgrs_tile")
        if not tile or not tile.startswith("49"):
            continue
        if abs((item.datetime - target).total_seconds()) > 1:
            continue
        previous = selected_by_tile.get(tile)
        if previous is None or item.id > previous.id:
            selected_by_tile[tile] = item
    selected = sorted(selected_by_tile.values(), key=lambda item: item.id)
    if not selected:
        raise RuntimeError(f"no Sentinel-2 items found for {acquisition}")
    return selected


def _open_vrts(
    stack: ExitStack,
    items: list[Any],
    asset_name: str,
    reference: rasterio.DatasetReader,
    resampling: Resampling,
) -> list[WarpedVRT]:
    vrts = []
    for item in items:
        source = stack.enter_context(rasterio.open(item.assets[asset_name].href))
        vrts.append(
            stack.enter_context(
                WarpedVRT(
                    source,
                    crs=reference.crs,
                    transform=reference.transform,
                    width=reference.width,
                    height=reference.height,
                    nodata=source.nodata,
                    resampling=resampling,
                )
            )
        )
    return vrts


def _open_aligned_sources(
    stack: ExitStack,
    source_paths: list[tuple[str, Path]],
    reference: rasterio.DatasetReader,
) -> list[rasterio.DatasetReader]:
    sources = []
    for source_id, source_path in source_paths:
        source = stack.enter_context(rasterio.open(source_path))
        if source.crs != reference.crs:
            raise ValueError(
                f"{source_id} CRS {source.crs} differs from "
                f"reference {reference.crs}"
            )
        if source.transform.b != 0 or source.transform.d != 0:
            raise ValueError(f"{source_id} has a rotated grid")
        x_ratio = source.transform.a / reference.transform.a
        y_ratio = source.transform.e / reference.transform.e
        x_offset = (
            source.transform.c - reference.transform.c
        ) / reference.transform.a
        y_offset = (
            source.transform.f - reference.transform.f
        ) / reference.transform.e
        values = (x_ratio, y_ratio, x_offset, y_offset)
        if not all(math.isclose(value, round(value), abs_tol=1e-7) for value in values):
            raise ValueError(
                f"{source_id} is not aligned to the reference grid"
            )
        sources.append(source)
    return sources


def _download_asset(
    source_id: str,
    asset_name: str,
    href: str,
    cache_dir: Path,
) -> tuple[str, str, Path]:
    output = cache_dir / f"{source_id}__{asset_name}.tif"
    partial = output.with_suffix(output.suffix + ".part")
    if output.exists() and output.stat().st_size > 0:
        return source_id, asset_name, output

    for attempt in range(1, 4):
        existing_size = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
        try:
            with requests.get(
                href,
                headers=headers,
                stream=True,
                timeout=(30, 180),
            ) as response:
                response.raise_for_status()
                append = existing_size > 0 and response.status_code == 206
                mode = "ab" if append else "wb"
                if existing_size > 0 and not append:
                    existing_size = 0
                with partial.open(mode) as stream:
                    for chunk in response.iter_content(4 * 1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            partial.replace(output)
            size_mb = output.stat().st_size / (1024 * 1024)
            print(
                f"Cached {source_id}/{asset_name}: {size_mb:.1f} MiB",
                flush=True,
            )
            return source_id, asset_name, output
        except (OSError, requests.RequestException):
            if attempt == 3:
                raise
            print(
                f"Retrying {source_id}/{asset_name} "
                f"({attempt}/3, {existing_size / (1024 * 1024):.1f} MiB cached)",
                flush=True,
            )
    raise RuntimeError(f"failed to cache {source_id}/{asset_name}")


def _cache_assets(
    items: list[Any],
    asset_names: list[str],
    cache_dir: Path,
) -> dict[str, list[tuple[str, Path]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = {asset_name: [] for asset_name in asset_names}
    jobs = []
    with ThreadPoolExecutor(max_workers=min(6, len(items) * len(asset_names))) as pool:
        for item in items:
            for asset_name in asset_names:
                jobs.append(
                    pool.submit(
                        _download_asset,
                        item.id,
                        asset_name,
                        item.assets[asset_name].href,
                        cache_dir,
                    )
                )
        for job in as_completed(jobs):
            source_id, asset_name, path = job.result()
            cached[asset_name].append((source_id, path))
    for asset_name in cached:
        cached[asset_name].sort(key=lambda value: value[0])
    return cached


def _remove_cached_assets(cached: dict[str, list[tuple[str, Path]]]) -> None:
    for sources in cached.values():
        for _, path in sources:
            path.unlink(missing_ok=True)


def _mosaic_aligned_window(
    sources: list[rasterio.DatasetReader],
    reference: rasterio.DatasetReader,
    window: rasterio.windows.Window,
    *,
    output_nodata: float,
    resampling: Resampling,
) -> np.ndarray:
    shape = (int(window.height), int(window.width))
    output = np.full(shape, output_nodata, dtype=np.float32)
    filled = np.zeros(shape, dtype=bool)
    target_left, target_bottom, target_right, target_top = window_bounds(
        window,
        reference.transform,
    )
    target_col_start = int(window.col_off)
    target_col_stop = target_col_start + int(window.width)
    target_row_start = int(window.row_off)
    target_row_stop = target_row_start + int(window.height)

    for source in sources:
        left = max(target_left, source.bounds.left)
        bottom = max(target_bottom, source.bounds.bottom)
        right = min(target_right, source.bounds.right)
        top = min(target_top, source.bounds.top)
        if left >= right or bottom >= top:
            continue

        global_col_start = max(
            target_col_start,
            int(round((left - reference.transform.c) / reference.transform.a)),
        )
        global_col_stop = min(
            target_col_stop,
            int(round((right - reference.transform.c) / reference.transform.a)),
        )
        global_row_start = max(
            target_row_start,
            int(round((top - reference.transform.f) / reference.transform.e)),
        )
        global_row_stop = min(
            target_row_stop,
            int(round((bottom - reference.transform.f) / reference.transform.e)),
        )
        if global_col_start >= global_col_stop or global_row_start >= global_row_stop:
            continue

        destination_window = Window(
            global_col_start,
            global_row_start,
            global_col_stop - global_col_start,
            global_row_stop - global_row_start,
        )
        read_bounds = window_bounds(destination_window, reference.transform)
        source_window = from_bounds(*read_bounds, transform=source.transform)
        read_height = global_row_stop - global_row_start
        read_width = global_col_stop - global_col_start
        source_values = source.read(
            1,
            window=source_window,
            out_shape=(read_height, read_width),
            masked=True,
            resampling=resampling,
        )
        values = np.asarray(source_values.data, dtype=np.float32)
        valid = ~np.ma.getmaskarray(source_values)
        valid &= np.isfinite(values) & (values != output_nodata)
        values[~valid] = output_nodata
        row_slice = slice(
            global_row_start - target_row_start,
            global_row_stop - target_row_start,
        )
        col_slice = slice(
            global_col_start - target_col_start,
            global_col_stop - target_col_start,
        )
        take = valid & ~filled[row_slice, col_slice]
        output[row_slice, col_slice][take] = values[take]
        filled[row_slice, col_slice][take] = True
    return output


def _mosaic_window(
    vrts: list[WarpedVRT],
    window: rasterio.windows.Window,
    *,
    output_nodata: float,
) -> np.ndarray:
    shape = (int(window.height), int(window.width))
    output = np.full(shape, output_nodata, dtype=np.float32)
    filled = np.zeros(shape, dtype=bool)
    for vrt in vrts:
        source = vrt.read(1, window=window, masked=True)
        values = np.asarray(source.filled(output_nodata), dtype=np.float32)
        valid = ~np.ma.getmaskarray(source)
        valid &= np.isfinite(values) & (values != output_nodata)
        take = valid & ~filled
        output[take] = values[take]
        filled[take] = True
    return output


def _study_mask(
    boundary_path: Path,
    reference: rasterio.DatasetReader,
) -> np.ndarray:
    geometry = (
        gpd.read_file(boundary_path)
        .to_crs(reference.crs)
        .geometry.make_valid()
        .union_all()
    )
    return rasterize(
        [(geometry, 1)],
        out_shape=(reference.height, reference.width),
        transform=reference.transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)


def _processing_windows(
    width: int,
    height: int,
    chunk_size: int = PROCESSING_CHUNK_SIZE,
) -> list[tuple[tuple[int, int], Window]]:
    windows = []
    for row_index, row_start in enumerate(range(0, height, chunk_size)):
        for column_index, column_start in enumerate(
            range(0, width, chunk_size)
        ):
            windows.append(
                (
                    (row_index, column_index),
                    Window(
                        column_start,
                        row_start,
                        min(chunk_size, width - column_start),
                        min(chunk_size, height - row_start),
                    ),
                )
            )
    return windows


def _percentiles_from_histogram(
    histogram: np.ndarray,
    lower: float,
    upper: float,
) -> tuple[float, float]:
    count = int(histogram.sum())
    if count == 0:
        raise ValueError("cannot calculate SAR percentiles without valid pixels")
    cumulative = np.cumsum(histogram)
    edges = np.linspace(lower, upper, len(histogram) + 1)
    indices = [
        int(np.searchsorted(cumulative, count * fraction, side="left"))
        for fraction in (0.01, 0.99)
    ]
    centers = (edges[:-1] + edges[1:]) / 2.0
    return float(centers[indices[0]]), float(centers[indices[1]])


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def build_outputs(arguments: argparse.Namespace) -> dict[str, Any]:
    catalog = Client.open(STAC_URL)
    sar_after_items = _select_sar_items(catalog, SAR_AFTER_DATE)
    sar_before_items = _select_sar_items(catalog, SAR_BEFORE_DATE)
    optical_groups = [
        (acquisition, _select_optical_items(catalog, acquisition))
        for acquisition in OPTICAL_ACQUISITIONS
    ]
    jrc_item = next(
        catalog.search(
            collections=["jrc-gsw"],
            bbox=[108.7938338, 22.4871194, 109.6181782, 23.1370098],
        ).items()
    )

    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = arguments.cache_dir or arguments.output_dir / ".source_cache"
    stack_path = arguments.output_dir / "hengzhou_stack_final_v01.tif"
    water_path = arguments.output_dir / "permanent_water_final_v01.tif"

    with rasterio.open(arguments.reference) as reference:
        reference_crs = reference.crs.to_string()
        reference_transform = [
            float(reference.transform.a),
            float(reference.transform.b),
            float(reference.transform.c),
            float(reference.transform.d),
            float(reference.transform.e),
            float(reference.transform.f),
        ]
        reference_width = reference.width
        reference_height = reference.height
        study = _study_mask(arguments.boundary, reference)
        study_count = int(study.sum())
        stack_profile = reference.profile.copy()
        stack_profile.update(
            count=6,
            dtype="float32",
            nodata=FLOAT_NODATA,
            compress="deflate",
            predictor=3,
            tiled=True,
            blockxsize=512,
            blockysize=512,
            BIGTIFF="YES",
        )
        water_profile = reference.profile.copy()
        water_profile.update(
            count=1,
            dtype="uint8",
            nodata=MASK_NODATA,
            compress="deflate",
            tiled=True,
            blockxsize=512,
            blockysize=512,
        )

        histogram = np.zeros(9000, dtype=np.int64)
        histogram_range = (-60.0, 30.0)
        optical_source_counts = {
            acquisition: 0 for acquisition in OPTICAL_ACQUISITIONS
        }
        optical_unfilled = 0
        sar_valid_count = 0
        water_count = 0
        descriptions = [
            "after_sar_uint8",
            "before_red",
            "before_green",
            "before_blue",
            "before_nir",
            "after_sar_db",
        ]

        print("Processing Sentinel-1 RTC...", flush=True)
        signed_sar_after = [pc.sign(item) for item in sar_after_items]
        cached_sar = _cache_assets(signed_sar_after, ["vv"], cache_dir)
        sar_succeeded = False
        try:
            with ExitStack() as resources:
                sar_after_sources = _open_aligned_sources(
                    resources,
                    cached_sar["vv"],
                    reference,
                )
                with rasterio.open(stack_path, "w", **stack_profile) as destination:
                    for index, description in enumerate(descriptions, start=1):
                        destination.set_band_description(index, description)
                    windows = _processing_windows(
                        destination.width,
                        destination.height,
                    )
                    total_windows = len(windows)
                    for window_index, (_, window) in enumerate(windows, start=1):
                        row_start = int(window.row_off)
                        row_stop = row_start + int(window.height)
                        col_start = int(window.col_off)
                        col_stop = col_start + int(window.width)
                        mask = study[row_start:row_stop, col_start:col_stop]

                        gamma0 = _mosaic_aligned_window(
                            sar_after_sources,
                            reference,
                            window,
                            output_nodata=FLOAT_NODATA,
                            resampling=Resampling.bilinear,
                        )
                        sar_db = sar_gamma0_to_db(gamma0)
                        sar_db[~mask] = FLOAT_NODATA
                        valid_sar = sar_db != FLOAT_NODATA
                        sar_valid_count += int(valid_sar.sum())
                        if valid_sar.any():
                            histogram += np.histogram(
                                sar_db[valid_sar],
                                bins=len(histogram),
                                range=histogram_range,
                            )[0]
                        destination.write(sar_db, 6, window=window)
                        empty = np.full(
                            mask.shape,
                            FLOAT_NODATA,
                            dtype=np.float32,
                        )
                        for band_index in range(1, 6):
                            destination.write(empty, band_index, window=window)

                        if (
                            window_index % 10 == 0
                            or window_index == total_windows
                        ):
                            print(
                                f"Sentinel-1 {window_index}/{total_windows}",
                                flush=True,
                            )
            sar_succeeded = True
        finally:
            if sar_succeeded:
                _remove_cached_assets(cached_sar)
            else:
                print(f"Retained interrupted SAR cache in {cache_dir}", flush=True)

        p1, p99 = _percentiles_from_histogram(
            histogram,
            *histogram_range,
        )
        print(f"SAR stretch p1={p1:.4f}, p99={p99:.4f}", flush=True)
        with rasterio.open(stack_path, "r+") as destination:
            for window_index, (_, window) in enumerate(windows, start=1):
                sar_db = destination.read(6, window=window)
                sar_uint8 = percentile_stretch_uint8(sar_db, p1, p99)
                destination.write(sar_uint8, 1, window=window)
                if window_index % 50 == 0 or window_index == total_windows:
                    print(
                        f"SAR stretch {window_index}/{total_windows}",
                        flush=True,
                    )

        for date_index, (acquisition, items) in enumerate(
            optical_groups,
            start=1,
        ):
            print(
                f"Processing Sentinel-2 composite date "
                f"{date_index}/{len(optical_groups)}: {acquisition}",
                flush=True,
            )
            signed_items = [pc.sign(item) for item in items]
            cached_optical = _cache_assets(
                signed_items,
                ["SCL", *OPTICAL_ASSETS],
                cache_dir,
            )
            optical_succeeded = False
            try:
                with ExitStack() as resources:
                    scl_sources = _open_aligned_sources(
                        resources,
                        cached_optical["SCL"],
                        reference,
                    )
                    band_sources = {
                        asset: _open_aligned_sources(
                            resources,
                            cached_optical[asset],
                            reference,
                        )
                        for asset in OPTICAL_ASSETS
                    }
                    with rasterio.open(stack_path, "r+") as destination:
                        for window_index, (_, window) in enumerate(
                            windows,
                            start=1,
                        ):
                            row_start = int(window.row_off)
                            row_stop = row_start + int(window.height)
                            col_start = int(window.col_off)
                            col_stop = col_start + int(window.width)
                            mask = study[
                                row_start:row_stop,
                                col_start:col_stop,
                            ]
                            current = destination.read(
                                [2, 3, 4, 5],
                                window=window,
                            )
                            filled = np.all(current != FLOAT_NODATA, axis=0)
                            scl = _mosaic_aligned_window(
                                scl_sources,
                                reference,
                                window,
                                output_nodata=0,
                                resampling=Resampling.nearest,
                            ).astype(np.uint8)
                            clear = (
                                mask
                                & (scl > 0)
                                & ~np.isin(scl, CLOUD_SCL_CLASSES)
                                & ~filled
                            )
                            if clear.any():
                                values = [
                                    _mosaic_aligned_window(
                                        band_sources[asset],
                                        reference,
                                        window,
                                        output_nodata=FLOAT_NODATA,
                                        resampling=Resampling.bilinear,
                                    )
                                    for asset in OPTICAL_ASSETS
                                ]
                                valid_all = clear.copy()
                                for value in values:
                                    valid_all &= value > 0
                                for band_index, value in enumerate(values):
                                    scaled = optical_reflectance_to_uint8(value)
                                    current[band_index][valid_all] = scaled[
                                        valid_all
                                    ]
                                destination.write(
                                    current,
                                    indexes=[2, 3, 4, 5],
                                    window=window,
                                )
                                optical_source_counts[acquisition] += int(
                                    valid_all.sum()
                                )
                            if (
                                window_index % 10 == 0
                                or window_index == total_windows
                            ):
                                print(
                                    f"Optical date {date_index}: "
                                    f"{window_index}/{total_windows}",
                                    flush=True,
                                )
                optical_succeeded = True
            finally:
                if optical_succeeded:
                    _remove_cached_assets(cached_optical)
                else:
                    print(
                        f"Retained interrupted optical cache in {cache_dir}",
                        flush=True,
                    )

        with rasterio.open(stack_path, "r+") as destination:
            for _, window in windows:
                row_start = int(window.row_off)
                row_stop = row_start + int(window.height)
                col_start = int(window.col_off)
                col_stop = col_start + int(window.width)
                mask = study[row_start:row_stop, col_start:col_stop]
                filled = destination.read(2, window=window) != FLOAT_NODATA
                optical_unfilled += int(np.count_nonzero(mask & ~filled))
            destination.update_tags(
                data_status="final",
                label_semantics="model_input",
                sar_p1_db=p1,
                sar_p99_db=p99,
            )

        print("Processing JRC permanent water...", flush=True)
        with ExitStack() as resources:
            signed_jrc = pc.sign(jrc_item)
            jrc_vrts = _open_vrts(
                resources,
                [signed_jrc],
                "occurrence",
                reference,
                Resampling.nearest,
            )
            with rasterio.open(water_path, "w", **water_profile) as water_output:
                water_output.set_band_description(1, "permanent_water")
                for window_index, (_, window) in enumerate(windows, start=1):
                    row_start = int(window.row_off)
                    row_stop = row_start + int(window.height)
                    col_start = int(window.col_off)
                    col_stop = col_start + int(window.width)
                    mask = study[row_start:row_stop, col_start:col_stop]
                    occurrence = _mosaic_window(
                        jrc_vrts,
                        window,
                        output_nodata=FLOAT_NODATA,
                    )
                    water = np.full(mask.shape, MASK_NODATA, dtype=np.uint8)
                    valid_water = mask & (occurrence >= 0)
                    water[valid_water] = (
                        occurrence[valid_water] >= PERMANENT_WATER_THRESHOLD
                    ).astype(np.uint8)
                    water_count += int(np.count_nonzero(water == 1))
                    water_output.write(water, 1, window=window)
                    print(
                        f"JRC water {window_index}/{total_windows}",
                        flush=True,
                    )
                water_output.update_tags(
                    data_status="final",
                    source="JRC Global Surface Water occurrence",
                    occurrence_threshold=PERMANENT_WATER_THRESHOLD,
                )

    metadata = {
        "crs": reference_crs,
        "resolution_m": 10,
        "transform": reference_transform,
        "width": reference_width,
        "height": reference_height,
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
            "sar_db_percentiles": [p1, p99],
            "sar_uint8_formula": (
                "clip(round((dB - p1) / (p99 - p1) * 255), 0, 255)"
            ),
            "optical": {"gain": 0.0255, "offset": 0},
        },
        "source_image_dates": {
            "sar_before": SAR_BEFORE_DATE,
            "sar_after": SAR_AFTER_DATE,
            "optical_before": [value[:10] for value in OPTICAL_ACQUISITIONS],
        },
        "permanent_water_threshold": PERMANENT_WATER_THRESHOLD,
        "grid_1km": {
            "origin_m": [reference_transform[2], reference_transform[5]],
            "px_per_cell": 100,
        },
        "provenance": {
            "stac_catalog": STAC_URL,
            "sar_before_products": [
                _item_summary(item) for item in sar_before_items
            ],
            "sar_after_products": [
                _item_summary(item) for item in sar_after_items
            ],
            "optical_products": [
                _item_summary(item)
                for _, items in optical_groups
                for item in items
            ],
            "permanent_water_product": _item_summary(jrc_item),
            "orbit_direction": SAR_ORBIT_STATE,
            "relative_orbit": SAR_RELATIVE_ORBIT,
            "polarization": "VV",
        },
        "processing": {
            "sar": (
                "Planetary Computer Sentinel-1 RTC gamma0 VV; "
                "10*log10(gamma0); 1st/99th percentile stretch"
            ),
            "optical": (
                "Sentinel-2 L2A cloud-free temporal composite in listed order; "
                "SCL classes 1,3,8,9,10,11 excluded; B04/B03/B02/B08"
            ),
            "permanent_water": "JRC occurrence >= 90%; nearest-neighbor",
            "continuous_resampling": "bilinear",
            "mask_resampling": "nearest",
        },
        "quality": {
            "study_pixel_count": study_count,
            "sar_valid_pixel_count": sar_valid_count,
            "sar_valid_fraction": sar_valid_count / study_count,
            "optical_source_pixel_counts": optical_source_counts,
            "optical_unfilled_pixel_count": optical_unfilled,
            "optical_valid_fraction": 1.0 - optical_unfilled / study_count,
            "permanent_water_pixel_count": water_count,
            "permanent_water_area_km2": water_count * 0.0001,
        },
        "files": {
            "reference_grid": str(arguments.reference),
            "input_stack": str(stack_path),
            "permanent_water": str(water_path),
        },
        "data_status": "final",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(arguments.metadata, metadata)
    _write_json(arguments.output_dir / "hengzhou_input_metadata.json", metadata)
    return metadata


def verify_outputs(arguments: argparse.Namespace) -> dict[str, Any]:
    stack_path = arguments.output_dir / "hengzhou_stack_final_v01.tif"
    water_path = arguments.output_dir / "permanent_water_final_v01.tif"
    with (
        rasterio.open(arguments.reference) as reference,
        rasterio.open(stack_path) as stack,
        rasterio.open(water_path) as water,
    ):
        signature = (
            reference.crs,
            reference.transform,
            reference.width,
            reference.height,
        )
        for name, dataset in [("stack", stack), ("permanent_water", water)]:
            candidate = (
                dataset.crs,
                dataset.transform,
                dataset.width,
                dataset.height,
            )
            if candidate != signature:
                raise ValueError(f"{name} spatial signature differs from reference")
        if stack.count != 6 or stack.dtypes != ("float32",) * 6:
            raise ValueError("input stack must contain six float32 bands")
        if stack.nodata != FLOAT_NODATA:
            raise ValueError("input stack NoData must be -9999")
        if water.count != 1 or water.dtypes[0] != "uint8":
            raise ValueError("permanent water must be a single uint8 band")
        if water.nodata != MASK_NODATA:
            raise ValueError("permanent water NoData must be 255")

        band_min = [math.inf] * 6
        band_max = [-math.inf] * 6
        invalid_water_values: set[int] = set()
        for _, window in stack.block_windows(1):
            for index in range(1, 7):
                values = stack.read(index, window=window)
                valid = values != FLOAT_NODATA
                if valid.any():
                    band_min[index - 1] = min(
                        band_min[index - 1],
                        float(values[valid].min()),
                    )
                    band_max[index - 1] = max(
                        band_max[index - 1],
                        float(values[valid].max()),
                    )
            invalid_water_values.update(
                int(value)
                for value in np.unique(water.read(1, window=window))
                if value not in (0, 1, MASK_NODATA)
            )
        if invalid_water_values:
            raise ValueError(
                f"unexpected permanent-water values: {invalid_water_values}"
            )
        for index in range(5):
            if band_min[index] < 0 or band_max[index] > 255:
                raise ValueError(f"stack band {index + 1} is outside 0..255")
        if band_min[5] < -100 or band_max[5] > 100:
            raise ValueError("SAR dB band has an implausible range")

        result = {
            "spatial_signature_matches_reference": True,
            "stack": {
                "count": stack.count,
                "dtype": stack.dtypes[0],
                "nodata": stack.nodata,
                "band_descriptions": list(stack.descriptions),
                "valid_ranges": [
                    [band_min[index], band_max[index]] for index in range(6)
                ],
            },
            "permanent_water": {
                "dtype": water.dtypes[0],
                "nodata": water.nodata,
                "allowed_values": [0, 1, 255],
            },
        }
    _write_json(arguments.verification, result)
    _write_json(
        arguments.output_dir / "hengzhou_inputs_verification_final_v01.json",
        result,
    )
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reference",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "processed"
        / "hengzhou_reference_grid_final_v01.tif",
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "external"
        / "hengzhou_boundary_osm_relation_2775762.geojson",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "rasters",
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
        "--verification",
        type=Path,
        default=PROJECT_ROOT
        / "outputs"
        / "tables"
        / "hengzhou_inputs_verification_final_v01.json",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Temporary source cache; defaults to OUTPUT_DIR/.source_cache",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    metadata = build_outputs(arguments)
    verification = verify_outputs(arguments)
    print(json.dumps(metadata["quality"], ensure_ascii=False, indent=2))
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
