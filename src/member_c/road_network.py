"""Optional formal road-network travel-time builder using OSMnx.

The module imports OSMnx only when executed, so the rest of the member C pipeline
remains usable before GIS dependencies are installed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import networkx as nx
import pandas as pd

from src.member_c.utils import read_table, require_columns, write_table


def build_road_matrix(
    graphml: str | Path,
    grids: pd.DataFrame,
    sites: pd.DataFrame,
    *,
    point_crs: str,
) -> pd.DataFrame:
    try:
        import osmnx as ox
    except ImportError as exc:
        raise RuntimeError("Install osmnx before building the formal road matrix") from exc

    require_columns(grids, ["grid_id", "center_x", "center_y"], "grids")
    require_columns(sites, ["site_id", "x", "y"], "sites")
    graph = ox.load_graphml(graphml)
    graph_crs = str(graph.graph.get("crs", ""))
    if graph_crs.lower() != point_crs.lower():
        graph = ox.project_graph(graph, to_crs=point_crs)
    if not all("travel_time" in data for _, _, data in graph.edges(data=True)):
        graph = ox.routing.add_edge_speeds(graph)
        graph = ox.routing.add_edge_travel_times(graph)

    grid_nodes = ox.distance.nearest_nodes(
        graph,
        grids["center_x"].to_numpy(float),
        grids["center_y"].to_numpy(float),
    )
    site_nodes = ox.distance.nearest_nodes(
        graph,
        sites["x"].to_numpy(float),
        sites["y"].to_numpy(float),
    )

    rows = []
    for grid_id, source in zip(grids["grid_id"].astype(str), grid_nodes):
        lengths = nx.single_source_dijkstra_path_length(graph, source, weight="travel_time")
        for site_id, target in zip(sites["site_id"].astype(str), site_nodes):
            seconds = lengths.get(target)
            rows.append(
                {
                    "grid_id": grid_id,
                    "site_id": site_id,
                    "travel_time_min": float(seconds / 60.0) if seconds is not None else 0.0,
                    "reachable": seconds is not None,
                    "matrix_source": "osm_shortest_path",
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphml", required=True)
    parser.add_argument("--grids", required=True)
    parser.add_argument("--sites", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--point-crs",
        required=True,
        help="CRS used by grid center_x/center_y and site x/y, e.g. EPSG:32649",
    )
    args = parser.parse_args()
    matrix = build_road_matrix(
        args.graphml,
        read_table(args.grids),
        read_table(args.sites),
        point_crs=args.point_crs,
    )
    write_table(matrix, args.output)
    print(f"Wrote {len(matrix)} road travel-time pairs to {args.output}")


if __name__ == "__main__":
    main()
