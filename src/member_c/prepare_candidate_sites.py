"""Convert OSM facilities to the raw candidate-site contract and sample safety rasters."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _sample_raster(points, raster_path: str) -> np.ndarray:
    try:
        import rasterio
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError("Install rasterio and pyproj before sampling safety rasters") from exc

    with rasterio.open(raster_path) as source:
        transformer = Transformer.from_crs(points.crs, source.crs, always_xy=True)
        x, y = transformer.transform(points.geometry.x.to_numpy(), points.geometry.y.to_numpy())
        values = [value[0] for value in source.sample(zip(x, y), masked=True)]
    return np.array(
        [np.nan if np.ma.is_masked(value) else float(value) for value in values],
        dtype=float,
    )


def prepare(
    facilities_path: str,
    flood_probability: str,
    uncertainty: str,
    dem: str,
    output_crs: str,
    default_capacity: float,
) -> pd.DataFrame:
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise RuntimeError("Install geopandas before preparing candidate sites") from exc

    facilities = gpd.read_file(facilities_path)
    if facilities.crs is None:
        raise ValueError("Facility layer has no CRS")
    facilities = facilities.loc[facilities.geometry.notna()].copy()
    polygon_mask = ~facilities.geometry.geom_type.isin(["Point", "MultiPoint"])
    facilities.loc[polygon_mask, "geometry"] = (
        facilities.loc[polygon_mask, "geometry"].representative_point()
    )
    facilities = facilities.to_crs(output_crs).reset_index(drop=True)

    facilities["site_id"] = [
        f"OSM_{value}" if pd.notna(value) else f"SITE_{index:05d}"
        for index, value in enumerate(
            facilities.get("osmid", pd.Series([None] * len(facilities)))
        )
    ]
    type_columns = [
        column for column in ["amenity", "leisure", "emergency"] if column in facilities
    ]
    if type_columns:
        facilities["site_type"] = (
            facilities[type_columns].bfill(axis=1).iloc[:, 0].fillna("public_facility")
        )
    else:
        facilities["site_type"] = "public_facility"

    facilities["x"] = facilities.geometry.x
    facilities["y"] = facilities.geometry.y
    facilities["flood_prob"] = _sample_raster(facilities, flood_probability)
    facilities["uncertainty"] = _sample_raster(facilities, uncertainty)
    facilities["elevation_m"] = _sample_raster(facilities, dem)
    facilities["capacity"] = float(default_capacity)
    facilities["road_connected"] = True
    result = facilities[
        [
            "site_id",
            "site_type",
            "x",
            "y",
            "capacity",
            "flood_prob",
            "uncertainty",
            "elevation_m",
            "road_connected",
        ]
    ].copy()
    return result.dropna(subset=["flood_prob", "uncertainty", "elevation_m"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", required=True)
    parser.add_argument("--flood-probability", required=True)
    parser.add_argument("--uncertainty", required=True)
    parser.add_argument("--dem", required=True)
    parser.add_argument("--output-crs", required=True)
    parser.add_argument("--default-capacity", type=float, default=1000.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = prepare(
        args.facilities,
        args.flood_probability,
        args.uncertainty,
        args.dem,
        args.output_crs,
        args.default_capacity,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    print(f"Wrote {len(result)} raw candidate sites to {args.output}")


if __name__ == "__main__":
    main()
