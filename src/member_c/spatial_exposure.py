"""Aggregate OSM buildings, roads, facilities, and population into B's grid."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _read_and_project(path: str, crs):
    import geopandas as gpd

    layer = gpd.read_file(path)
    if layer.crs is None:
        raise ValueError(f"Layer has no CRS: {path}")
    return layer.to_crs(crs)


def aggregate_spatial_exposure(
    grid_path: str,
    buildings_path: str,
    roads_path: str,
    facilities_path: str,
    population_csv: str,
):
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise RuntimeError("Install geopandas before spatial exposure aggregation") from exc

    grids = gpd.read_file(grid_path)
    if grids.crs is None or not grids.crs.is_projected:
        raise ValueError("Assessment grid must use a projected CRS")
    if "grid_id" not in grids or grids["grid_id"].duplicated().any():
        raise ValueError("Assessment grid requires unique grid_id values")
    grids["grid_id"] = grids["grid_id"].astype(str)
    result = grids.copy()

    buildings = _read_and_project(buildings_path, grids.crs)
    if not buildings.empty:
        buildings = buildings.loc[buildings.geometry.notna()].copy()
        buildings["_building_id"] = buildings.index.astype(str)
        buildings["_area_km2"] = buildings.geometry.area / 1_000_000.0
        building_points = buildings.copy()
        building_points.geometry = buildings.geometry.representative_point()
        joined = gpd.sjoin(
            building_points[["_building_id", "_area_km2", "geometry"]],
            grids[["grid_id", "geometry"]],
            predicate="within",
            how="inner",
        )
        building_stats = joined.groupby("grid_id").agg(
            total_buildings=("_building_id", "nunique"),
            building_area_km2=("_area_km2", "sum"),
        )
    else:
        building_stats = pd.DataFrame(columns=["total_buildings", "building_area_km2"])

    roads = _read_and_project(roads_path, grids.crs)
    if not roads.empty:
        road_intersections = gpd.overlay(
            roads[["geometry"]],
            grids[["grid_id", "geometry"]],
            how="intersection",
            keep_geom_type=False,
        )
        road_intersections["_length_km"] = road_intersections.geometry.length / 1000.0
        road_stats = road_intersections.groupby("grid_id").agg(
            total_road_km=("_length_km", "sum")
        )
    else:
        road_stats = pd.DataFrame(columns=["total_road_km"])

    facilities = _read_and_project(facilities_path, grids.crs)
    if not facilities.empty:
        facility_points = facilities.copy()
        non_points = ~facility_points.geometry.geom_type.isin(["Point", "MultiPoint"])
        facility_points.loc[non_points, "geometry"] = (
            facility_points.loc[non_points, "geometry"].representative_point()
        )
        facility_points["_facility_id"] = facility_points.index.astype(str)
        facility_join = gpd.sjoin(
            facility_points[["_facility_id", "geometry"]],
            grids[["grid_id", "geometry"]],
            predicate="within",
            how="inner",
        )
        facility_stats = facility_join.groupby("grid_id").agg(
            total_facilities=("_facility_id", "nunique")
        )
    else:
        facility_stats = pd.DataFrame(columns=["total_facilities"])

    population = pd.read_csv(population_csv)
    if "grid_id" not in population:
        raise ValueError("Population CSV requires grid_id")
    population["grid_id"] = population["grid_id"].astype(str)

    for table in [building_stats, road_stats, facility_stats]:
        result = result.merge(table, left_on="grid_id", right_index=True, how="left")
    result = result.merge(population, on="grid_id", how="left")
    fill_zero = [
        "total_buildings",
        "building_area_km2",
        "total_road_km",
        "total_facilities",
        "total_population",
        "affected_population",
    ]
    for column in fill_zero:
        if column not in result:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)

    probability = result["mean_flood_prob"].clip(0.0, 1.0)
    result["affected_buildings"] = result["total_buildings"] * probability
    result["flooded_road_km"] = result["total_road_km"] * probability
    result["affected_facilities"] = result["total_facilities"] * probability
    result["road_density_km_per_km2"] = (
        result["total_road_km"] / result["grid_area_km2"].clip(lower=1e-9)
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", required=True)
    parser.add_argument("--buildings", required=True)
    parser.add_argument("--roads", required=True)
    parser.add_argument("--facilities", required=True)
    parser.add_argument("--population-csv", required=True)
    parser.add_argument("--output-gpkg", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args()
    result = aggregate_spatial_exposure(
        args.grid,
        args.buildings,
        args.roads,
        args.facilities,
        args.population_csv,
    )
    Path(args.output_gpkg).parent.mkdir(parents=True, exist_ok=True)
    result.to_file(args.output_gpkg, driver="GPKG")
    result.drop(columns="geometry").to_csv(args.output_csv, index=False)
    print(f"Wrote spatial exposure for {len(result)} grids")


if __name__ == "__main__":
    main()
