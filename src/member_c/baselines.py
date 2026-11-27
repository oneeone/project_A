"""Baseline rescue-site strategies for comparison with the MILP model."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from src.member_c.utils import require_columns


def evaluate_selected_sites(
    grids: pd.DataFrame,
    sites: pd.DataFrame,
    travel: pd.DataFrame,
    selected_site_ids: Iterable[str],
    *,
    max_travel_time_min: float,
) -> dict[str, float | int]:
    selected_ids = {str(value) for value in selected_site_ids}
    chosen = sites.loc[sites["site_id"].astype(str).isin(selected_ids)].copy()
    capacities = dict(zip(chosen["site_id"].astype(str), chosen["capacity"].astype(float)))
    remaining = capacities.copy()
    pairs = travel.copy()
    pairs["grid_id"] = pairs["grid_id"].astype(str)
    pairs["site_id"] = pairs["site_id"].astype(str)
    pairs = pairs.loc[
        pairs["site_id"].isin(selected_ids)
        & pairs["reachable"].astype(bool)
        & (pairs["travel_time_min"] <= max_travel_time_min)
    ]

    grid_info = grids.set_index(grids["grid_id"].astype(str))
    assigned = 0.0
    weighted_time = 0.0
    times: list[float] = []
    covered_by_grid: dict[str, float] = {}
    order = grids.sort_values(["risk_score", "demand_score"], ascending=False)
    for row in order.itertuples():
        grid_id = str(row.grid_id)
        demand_left = float(row.demand_score)
        options = pairs.loc[pairs["grid_id"] == grid_id].sort_values("travel_time_min")
        for option in options.itertuples():
            site_id = str(option.site_id)
            flow = min(demand_left, remaining.get(site_id, 0.0))
            if flow <= 0:
                continue
            assigned += flow
            covered_by_grid[grid_id] = covered_by_grid.get(grid_id, 0.0) + flow
            weighted_time += flow * float(option.travel_time_min)
            times.append(float(option.travel_time_min))
            remaining[site_id] -= flow
            demand_left -= flow
            if demand_left <= 1e-9:
                break

    total = float(grids["demand_score"].sum())
    risk_threshold = float(grids["risk_score"].quantile(0.75))
    high_risk = grids.loc[grids["risk_score"] >= risk_threshold]
    high_total = float(high_risk["demand_score"].sum())
    high_covered = sum(covered_by_grid.get(str(value), 0.0) for value in high_risk["grid_id"])
    assigned = min(max(assigned, 0.0), total)
    return {
        "selected_sites": len(selected_ids),
        "total_demand": total,
        "covered_demand": assigned,
        "coverage_ratio": min(max(assigned / max(total, 1e-12), 0.0), 1.0),
        "unmet_demand": max(total - assigned, 0.0),
        "weighted_mean_travel_time_min": weighted_time / max(assigned, 1e-12),
        "max_travel_time_min": max(times, default=0.0),
        "high_risk_coverage_ratio": min(
            max(high_covered / max(high_total, 1e-12), 0.0),
            1.0,
        ),
    }


def _site_scores(
    grids: pd.DataFrame,
    safe_sites: pd.DataFrame,
    travel: pd.DataFrame,
    *,
    max_travel_time_min: float,
    grid_weight_column: str,
) -> pd.Series:
    require_columns(grids, ["grid_id", grid_weight_column], "grids")
    pairs = travel.loc[
        travel["site_id"].astype(str).isin(set(safe_sites["site_id"].astype(str)))
        & travel["reachable"].astype(bool)
        & (travel["travel_time_min"] <= max_travel_time_min)
    ].copy()
    weights = dict(zip(grids["grid_id"].astype(str), grids[grid_weight_column].astype(float)))
    pairs["score"] = pairs["grid_id"].astype(str).map(weights).fillna(0.0)
    return pairs.groupby(pairs["site_id"].astype(str))["score"].sum()


def compare_baselines(
    grids: pd.DataFrame,
    sites: pd.DataFrame,
    travel: pd.DataFrame,
    *,
    max_sites: int = 8,
    max_travel_time_min: float = 30.0,
    random_repeats: int = 100,
    random_seed: int = 2026,
) -> pd.DataFrame:
    safe = sites.loc[sites["safe_flag"].astype(bool)].copy()
    if len(safe) < max_sites:
        max_sites = len(safe)
    if max_sites <= 0:
        raise ValueError("No safe candidate sites are available")

    rows: list[dict[str, float | int | str]] = []
    rng = np.random.default_rng(random_seed)
    random_results = []
    site_ids = safe["site_id"].astype(str).to_numpy()
    for _ in range(random_repeats):
        selected = rng.choice(site_ids, size=max_sites, replace=False)
        random_results.append(
            evaluate_selected_sites(
                grids,
                safe,
                travel,
                selected,
                max_travel_time_min=max_travel_time_min,
            )
        )
    random_frame = pd.DataFrame(random_results)
    random_row = {column: float(random_frame[column].mean()) for column in random_frame}
    random_row["strategy"] = "random_mean"
    rows.append(random_row)

    population_scores = _site_scores(
        grids,
        safe,
        travel,
        max_travel_time_min=max_travel_time_min,
        grid_weight_column="affected_population",
    )
    population_selected = population_scores.nlargest(max_sites).index
    population_metrics = evaluate_selected_sites(
        grids,
        safe,
        travel,
        population_selected,
        max_travel_time_min=max_travel_time_min,
    )
    population_metrics["strategy"] = "population_priority"
    rows.append(population_metrics)

    weighted = grids.copy()
    weighted["_risk_demand"] = weighted["demand_score"] * weighted["risk_score"]
    risk_scores = _site_scores(
        weighted,
        safe,
        travel,
        max_travel_time_min=max_travel_time_min,
        grid_weight_column="_risk_demand",
    )
    risk_selected = risk_scores.nlargest(max_sites).index
    risk_metrics = evaluate_selected_sites(
        grids,
        safe,
        travel,
        risk_selected,
        max_travel_time_min=max_travel_time_min,
    )
    risk_metrics["strategy"] = "risk_priority"
    rows.append(risk_metrics)
    return pd.DataFrame(rows)
