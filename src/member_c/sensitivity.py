"""Sensitivity analysis for site count, travel limit, and assumed capacity."""

from __future__ import annotations

import pandas as pd

from src.member_c.facility_location import solve_facility_location


def run_sensitivity(
    grids: pd.DataFrame,
    sites: pd.DataFrame,
    travel: pd.DataFrame,
    *,
    site_counts: list[int],
    travel_limits: list[float],
    capacities: list[float],
    time_penalty_weight: float = 0.05,
) -> pd.DataFrame:
    rows: list[dict] = []
    for max_sites in site_counts:
        for travel_limit in travel_limits:
            for capacity in capacities:
                scenario_sites = sites.copy()
                scenario_sites["capacity"] = float(capacity)
                try:
                    _, _, metrics = solve_facility_location(
                        grids,
                        scenario_sites,
                        travel,
                        max_sites=int(max_sites),
                        max_travel_time_min=float(travel_limit),
                        time_penalty_weight=float(time_penalty_weight),
                    )
                    rows.append(
                        {
                            "max_sites": max_sites,
                            "travel_limit_min": travel_limit,
                            "capacity": capacity,
                            "status": "ok",
                            **metrics,
                        }
                    )
                except (ValueError, RuntimeError) as exc:
                    rows.append(
                        {
                            "max_sites": max_sites,
                            "travel_limit_min": travel_limit,
                            "capacity": capacity,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
    return pd.DataFrame(rows)
