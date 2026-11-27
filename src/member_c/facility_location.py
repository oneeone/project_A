"""Question 5: capacitated maximum-coverage rescue-site optimization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from src.member_c.schemas import validate_candidate_sites
from src.member_c.travel_time import validate_travel_matrix
from src.member_c.utils import read_table, require_columns, write_table


def _as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().isin({"1", "true", "yes", "y"})


def solve_facility_location(
    grids: pd.DataFrame,
    sites: pd.DataFrame,
    travel: pd.DataFrame,
    *,
    max_sites: int = 8,
    max_travel_time_min: float = 30.0,
    time_penalty_weight: float = 0.05,
    time_limit_seconds: float = 120.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int | str]]:
    """Solve a capacitated maximum-coverage model.

    Site variables are binary. Assignment variables are continuous demand flows, so a
    large grid can be served by more than one rescue site without violating capacities.
    """
    require_columns(grids, ["grid_id", "demand_score", "risk_score", "affected_population"], "grids")
    validate_candidate_sites(sites)
    validate_travel_matrix(travel, grids, sites)
    if max_sites <= 0:
        raise ValueError("max_sites must be positive")
    if max_travel_time_min <= 0:
        raise ValueError("max_travel_time_min must be positive")

    safe_sites = sites.loc[_as_bool(sites["safe_flag"])].copy().reset_index(drop=True)
    if safe_sites.empty:
        raise ValueError("No safe candidate sites are available")
    safe_sites["site_id"] = safe_sites["site_id"].astype(str)

    grid = grids.copy().reset_index(drop=True)
    grid["grid_id"] = grid["grid_id"].astype(str)
    grid["demand_score"] = pd.to_numeric(grid["demand_score"], errors="coerce").fillna(0.0).clip(lower=0.0)
    demand_by_grid = dict(zip(grid["grid_id"], grid["demand_score"]))
    capacity_by_site = dict(
        zip(safe_sites["site_id"], pd.to_numeric(safe_sites["capacity"]).astype(float))
    )
    safe_ids = set(safe_sites["site_id"])

    travel_work = travel.copy()
    travel_work["grid_id"] = travel_work["grid_id"].astype(str)
    travel_work["site_id"] = travel_work["site_id"].astype(str)
    travel_work["reachable"] = _as_bool(travel_work["reachable"])
    travel_work["travel_time_min"] = pd.to_numeric(travel_work["travel_time_min"])
    feasible = travel_work.loc[
        travel_work["site_id"].isin(safe_ids)
        & travel_work["reachable"]
        & (travel_work["travel_time_min"] <= max_travel_time_min)
        & travel_work["grid_id"].isin(demand_by_grid)
    ].copy()
    feasible = feasible.loc[feasible["grid_id"].map(demand_by_grid).gt(0)].reset_index(drop=True)
    if feasible.empty:
        raise ValueError("No reachable grid-site pairs satisfy the travel-time limit")

    site_ids = safe_sites["site_id"].tolist()
    site_index = {site_id: index for index, site_id in enumerate(site_ids)}
    pair_count = len(feasible)
    site_count = len(site_ids)
    variable_count = site_count + pair_count

    # x_j binary selection, q_ij continuous assigned demand.
    objective = np.zeros(variable_count)
    total_demand = max(float(grid["demand_score"].sum()), 1e-12)
    for pair_index, row in feasible.iterrows():
        normalized_time = float(row["travel_time_min"]) / max_travel_time_min
        objective[site_count + pair_index] = (
            -1.0 + time_penalty_weight * normalized_time
        ) / total_demand

    integrality = np.zeros(variable_count, dtype=int)
    integrality[:site_count] = 1
    lower_bounds = np.zeros(variable_count)
    upper_bounds = np.ones(variable_count)
    for pair_index, row in feasible.iterrows():
        upper_bounds[site_count + pair_index] = demand_by_grid[row["grid_id"]]

    rows: list[tuple[dict[int, float], float, float]] = []
    # At most max_sites.
    rows.append(({index: 1.0 for index in range(site_count)}, -np.inf, float(max_sites)))

    # Each grid receives at most its total demand.
    for grid_id, group in feasible.groupby("grid_id"):
        coefficients = {site_count + int(index): 1.0 for index in group.index}
        rows.append((coefficients, -np.inf, float(demand_by_grid[grid_id])))

    # Pair flow is zero unless its site is selected: q_ij <= demand_i * x_j.
    for pair_index, row in feasible.iterrows():
        demand = float(demand_by_grid[row["grid_id"]])
        coefficients = {
            site_count + pair_index: 1.0,
            site_index[row["site_id"]]: -demand,
        }
        rows.append((coefficients, -np.inf, 0.0))

    # Site capacity.
    for site_id, group in feasible.groupby("site_id"):
        coefficients = {site_count + int(index): 1.0 for index in group.index}
        coefficients[site_index[site_id]] = -float(capacity_by_site[site_id])
        rows.append((coefficients, -np.inf, 0.0))

    matrix = lil_matrix((len(rows), variable_count), dtype=float)
    lower = np.empty(len(rows))
    upper = np.empty(len(rows))
    for row_index, (coefficients, low, high) in enumerate(rows):
        for column, value in coefficients.items():
            matrix[row_index, column] = value
        lower[row_index] = low
        upper[row_index] = high

    result = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(lower_bounds, upper_bounds),
        constraints=LinearConstraint(matrix.tocsr(), lower, upper),
        options={"time_limit": float(time_limit_seconds), "presolve": True},
    )
    if result.x is None or not result.success:
        raise RuntimeError(f"Facility-location solver failed: {result.message}")

    selected_values = result.x[:site_count]
    selected = safe_sites.copy()
    selected["selected"] = selected_values >= 0.5

    assignment_rows: list[dict[str, Any]] = []
    flow_values = result.x[site_count:]
    for pair_index, flow in enumerate(flow_values):
        if flow <= 1e-7:
            continue
        row = feasible.iloc[pair_index]
        assignment_rows.append(
            {
                "grid_id": row["grid_id"],
                "site_id": row["site_id"],
                "travel_time_min": float(row["travel_time_min"]),
                "assigned_demand": float(flow),
                "covered": True,
            }
        )
    assignments = pd.DataFrame(
        assignment_rows,
        columns=["grid_id", "site_id", "travel_time_min", "assigned_demand", "covered"],
    )

    assigned_by_site = (
        assignments.groupby("site_id")["assigned_demand"].sum()
        if not assignments.empty
        else pd.Series(dtype=float)
    )
    selected["assigned_demand"] = selected["site_id"].map(assigned_by_site).fillna(0.0)
    selected["covered_population"] = selected["assigned_demand"]
    selected["utilization_ratio"] = selected["assigned_demand"] / selected["capacity"]
    if not assignments.empty:
        weighted_assignments = assignments.assign(
            weighted=lambda x: x["travel_time_min"] * x["assigned_demand"]
        )
        by_site = weighted_assignments.groupby("site_id")[["weighted", "assigned_demand"]].sum()
        mean_time = by_site["weighted"] / by_site["assigned_demand"].clip(lower=1e-12)
    else:
        mean_time = pd.Series(dtype=float)
    max_time = (
        assignments.groupby("site_id")["travel_time_min"].max()
        if not assignments.empty
        else pd.Series(dtype=float)
    )
    selected["mean_travel_time_min"] = selected["site_id"].map(mean_time)
    selected["max_travel_time_min"] = selected["site_id"].map(max_time)

    covered = float(assignments["assigned_demand"].sum()) if not assignments.empty else 0.0
    weighted_time_sum = (
        float((assignments["travel_time_min"] * assignments["assigned_demand"]).sum())
        if not assignments.empty
        else 0.0
    )
    grid_covered = (
        assignments.groupby("grid_id")["assigned_demand"].sum()
        if not assignments.empty
        else pd.Series(dtype=float)
    )
    risk_threshold = float(grid["risk_score"].quantile(0.75))
    high_risk_ids = set(grid.loc[grid["risk_score"] >= risk_threshold, "grid_id"])
    high_risk_demand = float(grid.loc[grid["grid_id"].isin(high_risk_ids), "demand_score"].sum())
    high_risk_covered = float(grid_covered.reindex(list(high_risk_ids), fill_value=0.0).sum())

    total_demand_value = float(grid["demand_score"].sum())
    covered = min(max(covered, 0.0), total_demand_value)
    metrics: dict[str, float | int | str] = {
        "solver": "scipy_milp",
        "solver_status": str(result.message),
        "selected_sites": int(selected["selected"].sum()),
        "total_demand": total_demand_value,
        "covered_demand": covered,
        "coverage_ratio": min(max(covered / total_demand, 0.0), 1.0),
        "unmet_demand": max(total_demand_value - covered, 0.0),
        "weighted_mean_travel_time_min": weighted_time_sum / max(covered, 1e-12),
        "max_travel_time_min": (
            float(assignments["travel_time_min"].max()) if not assignments.empty else 0.0
        ),
        "high_risk_coverage_ratio": min(
            max(high_risk_covered / max(high_risk_demand, 1e-12), 0.0),
            1.0,
        ),
        "max_sites_parameter": int(max_sites),
        "travel_limit_parameter_min": float(max_travel_time_min),
    }
    return selected, assignments, metrics


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("Optimization configuration must be a mapping")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grids", required=True)
    parser.add_argument("--sites", required=True)
    parser.add_argument("--travel", required=True)
    parser.add_argument("--config", default="configs/optimization.yaml")
    parser.add_argument("--selected-output", required=True)
    parser.add_argument("--assignment-output", required=True)
    parser.add_argument("--metrics-output", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    baseline = config.get("baseline", {})
    selected, assignments, metrics = solve_facility_location(
        read_table(args.grids),
        read_table(args.sites),
        read_table(args.travel),
        max_sites=int(baseline.get("max_sites", 8)),
        max_travel_time_min=float(baseline.get("max_travel_time_min", 30)),
        time_penalty_weight=float(baseline.get("time_penalty_weight", 0.05)),
    )
    write_table(selected, args.selected_output)
    write_table(assignments, args.assignment_output)
    metrics_path = Path(args.metrics_output)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
