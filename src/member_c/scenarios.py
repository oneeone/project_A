"""Optimistic, neutral, and pessimistic flood-grid scenarios."""

from __future__ import annotations

import pandas as pd


SCENARIO_FACTORS = {
    "optimistic": (-0.10, 0.85),
    "neutral": (0.00, 1.00),
    "pessimistic": (0.10, 1.15),
}


def apply_scenario(frame: pd.DataFrame, scenario: str) -> pd.DataFrame:
    if scenario not in SCENARIO_FACTORS:
        raise ValueError(f"Unknown scenario: {scenario}")
    probability_shift, extent_factor = SCENARIO_FACTORS[scenario]
    result = frame.copy()
    result["scenario"] = scenario
    result["mean_flood_prob"] = (result["mean_flood_prob"] + probability_shift).clip(0.0, 1.0)
    result["flood_ratio"] = (result["flood_ratio"] * extent_factor).clip(0.0, 1.0)
    result["flood_area_km2"] = (
        result["grid_area_km2"] * result["flood_ratio"]
    ).clip(lower=0.0)
    result["intensity_score"] = result["intensity_score"] * extent_factor
    # Recalculate probability-weighted exposure under the changed scenario.
    for column in [
        "affected_population",
        "affected_buildings",
        "flooded_road_km",
        "affected_facilities",
    ]:
        if column in result:
            result = result.drop(columns=column)
    return result
