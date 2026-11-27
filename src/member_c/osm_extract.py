"""Download OSM buildings, facilities, candidate sites, and a drive graph."""

from __future__ import annotations

import argparse
from pathlib import Path


FACILITY_TAGS = {
    "amenity": [
        "hospital",
        "clinic",
        "school",
        "college",
        "university",
        "community_centre",
        "townhall",
        "fire_station",
        "police",
        "shelter",
    ],
    "leisure": ["stadium", "sports_centre"],
    "emergency": True,
}


def extract(boundary_path: str, output_dir: str) -> None:
    try:
        import geopandas as gpd
        import osmnx as ox
    except ImportError as exc:
        raise RuntimeError("Install geopandas and osmnx before downloading OSM data") from exc

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    boundary = gpd.read_file(boundary_path)
    if boundary.crs is None:
        raise ValueError("Boundary layer has no CRS")
    boundary_wgs84 = boundary.to_crs(4326)
    polygon = (
        boundary_wgs84.geometry.union_all()
        if hasattr(boundary_wgs84.geometry, "union_all")
        else boundary_wgs84.geometry.unary_union
    )

    buildings = ox.features_from_polygon(polygon, tags={"building": True})
    facilities = ox.features_from_polygon(polygon, tags=FACILITY_TAGS)
    graph = ox.graph_from_polygon(polygon, network_type="drive", simplify=True)
    graph = ox.routing.add_edge_speeds(graph)
    graph = ox.routing.add_edge_travel_times(graph)

    if not buildings.empty:
        buildings.reset_index().to_file(output / "osm_buildings.gpkg", driver="GPKG")
    if not facilities.empty:
        facilities.reset_index().to_file(output / "osm_facilities.gpkg", driver="GPKG")
    nodes, edges = ox.graph_to_gdfs(graph)
    edges.reset_index().to_file(output / "osm_roads.gpkg", driver="GPKG")
    ox.save_graphml(graph, output / "osm_drive.graphml")
    projected_graph = ox.project_graph(graph)
    ox.save_graphml(projected_graph, output / "osm_drive_projected.graphml")
    print(f"Wrote OSM layers and road graph to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    extract(args.boundary, args.output_dir)


if __name__ == "__main__":
    main()
