"""Create or validate grid-to-site travel-time matrices."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from src.member_c.utils import assert_unique, read_table, require_columns, write_table


TRAVEL_COLUMNS = ["grid_id", "site_id", "travel_time_min", "reachable"]


def build_euclidean_mock_matrix(
    grids: pd.DataFrame,
    sites: pd.DataFrame,
    *,
    speed_kmh: float = 30.0,
    detour_factor: float = 1.25,
) -> pd.DataFrame:
    """Build a development-only matrix from projected x/y coordinates.

    This must not be used for final results; formal results require road shortest paths.
    """
    require_columns(grids, ["grid_id", "center_x", "center_y"], "grids")
    require_columns(sites, ["site_id", "x", "y"], "sites")
    assert_unique(grids, "grid_id", "grids")
    assert_unique(sites, "site_id", "sites")
    if speed_kmh <= 0 or detour_factor < 1:
        raise ValueError("speed_kmh must be positive and detour_factor must be >= 1")

    gx = grids["center_x"].to_numpy(float)[:, None]
    gy = grids["center_y"].to_numpy(float)[:, None]
    sx = sites["x"].to_numpy(float)[None, :]
    sy = sites["y"].to_numpy(float)[None, :]
    distance_km = np.sqrt((gx - sx) ** 2 + (gy - sy) ** 2) / 1000.0
    travel_minutes = distance_km * detour_factor / speed_kmh * 60.0

    rows = []
    for i, grid_id in enumerate(grids["grid_id"].astype(str)):
        for j, site_id in enumerate(sites["site_id"].astype(str)):
            rows.append(
                {
                    "grid_id": grid_id,
                    "site_id": site_id,
                    "travel_time_min": float(travel_minutes[i, j]),
                    "reachable": True,
                    "matrix_source": "mock_euclidean",
                }
            )
    return pd.DataFrame(rows)


def validate_travel_matrix(
    matrix: pd.DataFrame,
    grids: pd.DataFrame,
    sites: pd.DataFrame,
) -> None:
    require_columns(matrix, TRAVEL_COLUMNS, "travel_matrix")
    valid_grids = set(grids["grid_id"].astype(str))
    valid_sites = set(sites["site_id"].astype(str))
    if not set(matrix["grid_id"].astype(str)).issubset(valid_grids):
        raise ValueError("travel_matrix contains unknown grid_id values")
    if not set(matrix["site_id"].astype(str)).issubset(valid_sites):
        raise ValueError("travel_matrix contains unknown site_id values")
    duplicated = matrix.duplicated(["grid_id", "site_id"])
    if duplicated.any():
        raise ValueError("travel_matrix contains duplicate grid-site pairs")
    travel = pd.to_numeric(matrix["travel_time_min"], errors="coerce")
    if travel.isna().any() or (travel < 0).any():
        raise ValueError("travel_time_min must be finite and non-negative")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grids", required=True)
    parser.add_argument("--sites", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--speed-kmh", type=float, default=30.0)
    parser.add_argument("--detour-factor", type=float, default=1.25)
    parser.add_argument(
        "--allow-mock-euclidean",
        action="store_true",
        help="Required acknowledgement that the matrix is for development only",
    )
    args = parser.parse_args()
    if not args.allow_mock_euclidean:
        raise SystemExit("Refusing to create a mock matrix without --allow-mock-euclidean")
    grids = read_table(args.grids)
    sites = read_table(args.sites)
    matrix = build_euclidean_mock_matrix(
        grids,
        sites,
        speed_kmh=args.speed_kmh,
        detour_factor=args.detour_factor,
    )
    write_table(matrix, args.output)
    print(f"Wrote {len(matrix)} mock travel-time pairs to {args.output}")


if __name__ == "__main__":
    main()
