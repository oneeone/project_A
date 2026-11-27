"""Aggregate WorldPop and probability-weighted affected population by grid."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd


def aggregate_population(
    grid_path: str,
    population_raster: str,
    flood_probability_raster: str,
) -> pd.DataFrame:
    try:
        import geopandas as gpd
        import rasterio
        from rasterio.features import geometry_mask, geometry_window
        from rasterio.vrt import WarpedVRT
        from rasterio.enums import Resampling
    except ImportError as exc:
        raise RuntimeError("Install geopandas and rasterio before population aggregation") from exc

    grids = gpd.read_file(grid_path)
    if "grid_id" not in grids:
        raise ValueError("Grid layer must contain grid_id")
    if grids["grid_id"].duplicated().any():
        raise ValueError("grid_id must be unique")

    rows = []
    with rasterio.open(population_raster) as population, rasterio.open(
        flood_probability_raster
    ) as probability_source:
        projected_grids = grids.to_crs(population.crs)
        with WarpedVRT(
            probability_source,
            crs=population.crs,
            transform=population.transform,
            width=population.width,
            height=population.height,
            resampling=Resampling.bilinear,
            nodata=-9999,
        ) as probability:
            for grid_id, geometry in zip(projected_grids["grid_id"], projected_grids.geometry):
                try:
                    window = geometry_window(population, [geometry], pad_x=0, pad_y=0)
                except Exception:
                    rows.append(
                        {
                            "grid_id": str(grid_id),
                            "total_population": 0.0,
                            "affected_population": 0.0,
                        }
                    )
                    continue
                pop = population.read(1, window=window, masked=True).astype(float)
                prob = probability.read(1, window=window, masked=True).astype(float)
                transform = population.window_transform(window)
                inside = geometry_mask(
                    [geometry],
                    out_shape=pop.shape,
                    transform=transform,
                    invert=True,
                )
                pop_data = np.where(
                    inside & ~np.ma.getmaskarray(pop) & np.isfinite(pop.data),
                    np.maximum(pop.data, 0.0),
                    0.0,
                )
                prob_data = np.where(
                    inside & ~np.ma.getmaskarray(prob) & np.isfinite(prob.data),
                    np.clip(prob.data, 0.0, 1.0),
                    0.0,
                )
                rows.append(
                    {
                        "grid_id": str(grid_id),
                        "total_population": float(pop_data.sum()),
                        "affected_population": float((pop_data * prob_data).sum()),
                    }
                )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", required=True)
    parser.add_argument("--population-raster", required=True)
    parser.add_argument("--flood-probability", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = aggregate_population(
        args.grid,
        args.population_raster,
        args.flood_probability,
    )
    result.to_csv(args.output, index=False)
    print(f"Wrote population exposure for {len(result)} grids to {args.output}")


if __name__ == "__main__":
    main()
