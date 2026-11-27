"""Run the complete member C tabular risk and facility-location pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.member_c.baselines import compare_baselines
from src.member_c.candidate_sites import filter_candidate_sites
from src.member_c.facility_location import solve_facility_location
from src.member_c.risk_assessment import calculate_risk
from src.member_c.schemas import validate_assessment_grid
from src.member_c.scenarios import SCENARIO_FACTORS, apply_scenario
from src.member_c.sensitivity import run_sensitivity
from src.member_c.travel_time import (
    build_euclidean_mock_matrix,
    validate_travel_matrix,
)
from src.member_c.utils import read_table, write_table
from src.member_c.visualize import plot_risk_and_sites


def load_yaml(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping: {path}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment-grid", required=True)
    parser.add_argument("--raw-sites", required=True)
    parser.add_argument("--travel-matrix")
    parser.add_argument("--assessment-config", default="configs/assessment.yaml")
    parser.add_argument("--optimization-config", default="configs/optimization.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--allow-mock-euclidean", action="store_true")
    parser.add_argument("--with-sensitivity", action="store_true")
    parser.add_argument("--with-scenarios", action="store_true")
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    assessment_config = load_yaml(args.assessment_config)
    optimization_config = load_yaml(args.optimization_config)
    baseline = optimization_config["baseline"]

    assessment = read_table(args.assessment_grid)
    validate_assessment_grid(assessment, allow_mock=not args.formal)
    sites = filter_candidate_sites(read_table(args.raw_sites), optimization_config)
    write_table(sites, output / "candidate_sites.csv")

    if args.travel_matrix:
        travel = read_table(args.travel_matrix)
        validate_travel_matrix(travel, assessment, sites)
    else:
        if not args.allow_mock_euclidean:
            raise SystemExit(
                "A formal road matrix is required unless --allow-mock-euclidean is supplied"
            )
        travel = build_euclidean_mock_matrix(assessment, sites)
    write_table(travel, output / "travel_time_matrix.csv")

    safe_ids = set(sites.loc[sites["safe_flag"], "site_id"].astype(str))
    reachable = travel.loc[
        travel["site_id"].astype(str).isin(safe_ids)
        & travel["reachable"].astype(bool)
    ].copy()
    nearest = reachable.groupby(reachable["grid_id"].astype(str))["travel_time_min"].min()
    assessment = assessment.copy()
    assessment["nearest_safe_facility_time_min"] = (
        assessment["grid_id"].astype(str).map(nearest)
    )
    fallback_time = float(baseline["max_travel_time_min"]) * 2.0
    assessment["nearest_safe_facility_time_min"] = (
        assessment["nearest_safe_facility_time_min"].fillna(fallback_time)
    )
    risk = calculate_risk(assessment, assessment_config)
    write_table(risk, output / "risk_grid.csv")

    selected, assignments, metrics = solve_facility_location(
        risk,
        sites,
        travel,
        max_sites=int(baseline["max_sites"]),
        max_travel_time_min=float(baseline["max_travel_time_min"]),
        time_penalty_weight=float(baseline["time_penalty_weight"]),
    )
    write_table(selected, output / "selected_sites.csv")
    write_table(assignments, output / "service_assignment.csv")
    (output / "optimization_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    comparison = compare_baselines(
        risk,
        sites,
        travel,
        max_sites=int(baseline["max_sites"]),
        max_travel_time_min=float(baseline["max_travel_time_min"]),
        random_repeats=int(baseline["random_baseline_repeats"]),
        random_seed=int(baseline["random_seed"]),
    )
    optimized = pd.DataFrame([{"strategy": "optimized", **metrics}])
    comparison = pd.concat([comparison, optimized], ignore_index=True)
    write_table(comparison, output / "baseline_comparison.csv")

    if args.with_sensitivity:
        sensitivity_config = optimization_config["sensitivity"]
        sensitivity = run_sensitivity(
            risk,
            sites,
            travel,
            site_counts=[int(value) for value in sensitivity_config["max_sites"]],
            travel_limits=[
                float(value) for value in sensitivity_config["max_travel_time_min"]
            ],
            capacities=[float(value) for value in sensitivity_config["default_capacity"]],
            time_penalty_weight=float(baseline["time_penalty_weight"]),
        )
        write_table(sensitivity, output / "sensitivity_analysis.csv")

    if args.with_scenarios:
        scenario_rows = []
        for scenario in SCENARIO_FACTORS:
            scenario_risk = calculate_risk(apply_scenario(assessment, scenario), assessment_config)
            _, _, scenario_metrics = solve_facility_location(
                scenario_risk,
                sites,
                travel,
                max_sites=int(baseline["max_sites"]),
                max_travel_time_min=float(baseline["max_travel_time_min"]),
                time_penalty_weight=float(baseline["time_penalty_weight"]),
            )
            scenario_rows.append({"scenario": scenario, **scenario_metrics})
        write_table(pd.DataFrame(scenario_rows), output / "scenario_analysis.csv")

    plot_risk_and_sites(risk, selected, output / "risk_and_selected_sites.png")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
