"""Question 4: exposure, accessibility, risk, and rescue demand."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.member_c.schemas import validate_assessment_grid
from src.member_c.utils import minmax, read_table, require_columns, write_table


EXPOSURE_INPUT_COLUMNS = [
    "total_population",
    "total_buildings",
    "building_area_km2",
    "total_road_km",
    "total_facilities",
]


def _weights(config: dict[str, Any], key: str, defaults: dict[str, float]) -> dict[str, float]:
    supplied = config.get("risk", {}).get(key, defaults)
    result = {name: float(supplied.get(name, value)) for name, value in defaults.items()}
    total = sum(result.values())
    if total <= 0:
        raise ValueError(f"{key} weights must have a positive sum")
    return {name: value / total for name, value in result.items()}


def add_affected_exposure(frame: pd.DataFrame) -> pd.DataFrame:
    """Estimate affected exposure when pixel-level overlays are not supplied.

    Existing affected_* columns are preserved. Missing values use the grid mean flood
    probability, which is the documented fallback until pixel-level overlays are ready.
    """
    result = frame.copy()
    require_columns(result, EXPOSURE_INPUT_COLUMNS, "exposure_grid")
    probability = pd.to_numeric(result["mean_flood_prob"]).clip(0.0, 1.0)
    fallback = {
        "affected_population": "total_population",
        "affected_buildings": "total_buildings",
        "flooded_road_km": "total_road_km",
        "affected_facilities": "total_facilities",
    }
    for output, total in fallback.items():
        estimate = pd.to_numeric(result[total], errors="coerce").fillna(0.0) * probability
        if output not in result:
            result[output] = estimate
        else:
            result[output] = pd.to_numeric(result[output], errors="coerce").fillna(estimate)
        result[output] = result[output].clip(lower=0.0)
    return result


def calculate_risk(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Calculate hazard, exposure, accessibility, risk, and demand."""
    validate_assessment_grid(frame, allow_mock=True)
    result = add_affected_exposure(frame)
    if "road_density_km_per_km2" not in result:
        result["road_density_km_per_km2"] = (
            result["total_road_km"] / result["grid_area_km2"].clip(lower=1e-9)
        )
    if "mean_slope_degree" not in result:
        result["mean_slope_degree"] = 0.0
    if "nearest_safe_facility_time_min" not in result:
        raise ValueError(
            "nearest_safe_facility_time_min is required; derive it from the travel matrix"
        )

    hazard_weights = _weights(
        config,
        "hazard_weights",
        {"flood_area": 0.35, "flood_ratio": 0.35, "intensity": 0.20, "mean_probability": 0.10},
    )
    exposure_weights = _weights(
        config,
        "baseline_weights",
        {"population": 0.45, "buildings": 0.20, "roads": 0.15, "facilities": 0.20},
    )
    access_weights = _weights(
        config,
        "accessibility_weights",
        {"travel_time": 0.50, "inverse_road_density": 0.30, "slope": 0.20},
    )

    result["hazard_score"] = (
        hazard_weights["flood_area"] * minmax(result["flood_area_km2"])
        + hazard_weights["flood_ratio"] * minmax(result["flood_ratio"])
        + hazard_weights["intensity"] * minmax(result["intensity_score"])
        + hazard_weights["mean_probability"] * result["mean_flood_prob"].clip(0.0, 1.0)
    ).clip(0.0, 1.0)

    result["exposure_score"] = (
        exposure_weights["population"] * minmax(result["affected_population"])
        + exposure_weights["buildings"] * minmax(result["affected_buildings"])
        + exposure_weights["roads"] * minmax(result["flooded_road_km"])
        + exposure_weights["facilities"] * minmax(result["affected_facilities"])
    ).clip(0.0, 1.0)

    result["accessibility_score"] = (
        access_weights["travel_time"] * minmax(result["nearest_safe_facility_time_min"])
        + access_weights["inverse_road_density"] * (1.0 - minmax(result["road_density_km_per_km2"]))
        + access_weights["slope"] * minmax(result["mean_slope_degree"])
    ).clip(0.0, 1.0)

    penalty = float(config.get("risk", {}).get("accessibility_penalty", 0.5))
    raw = (
        result["hazard_score"]
        * result["exposure_score"]
        * (1.0 + penalty * result["accessibility_score"])
    )
    result["risk_score"] = minmax(raw)

    demand_risk_weight = float(config.get("risk", {}).get("demand_risk_weight", 0.5))
    demand_risk_weight = min(max(demand_risk_weight, 0.0), 1.0)
    result["demand_score"] = result["affected_population"] * (
        (1.0 - demand_risk_weight) + demand_risk_weight * result["risk_score"]
    )
    result["demand_score"] = result["demand_score"].clip(lower=0.0)
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("Assessment configuration must be a mapping")
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="B assessment grid CSV/Parquet")
    parser.add_argument("--config", default="configs/assessment.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--formal",
        action="store_true",
        help="Reject any input row whose data_status is mock",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    frame = read_table(args.input)
    validate_assessment_grid(frame, allow_mock=not args.formal)
    result = calculate_risk(frame, load_config(args.config))
    write_table(result, args.output)
    print(f"Wrote {len(result)} risk rows to {args.output}")


if __name__ == "__main__":
    main()
