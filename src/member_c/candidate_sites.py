"""Question 5: validate, deduplicate, and safety-filter rescue sites."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from src.member_c.schemas import validate_candidate_sites
from src.member_c.utils import read_table, require_columns, write_table


RAW_SITE_COLUMNS = [
    "site_id",
    "site_type",
    "x",
    "y",
    "flood_prob",
    "uncertainty",
    "elevation_m",
    "road_connected",
]


def _as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().isin({"1", "true", "yes", "y"})


def deduplicate_by_distance(frame: pd.DataFrame, distance_m: float = 20.0) -> pd.DataFrame:
    """Keep the first point within distance_m, after exact site_id deduplication."""
    result = frame.drop_duplicates("site_id", keep="first").reset_index(drop=True)
    keep: list[int] = []
    xy = result[["x", "y"]].to_numpy(float)
    for index, point in enumerate(xy):
        if not keep:
            keep.append(index)
            continue
        distance = np.sqrt(((xy[keep] - point) ** 2).sum(axis=1))
        if np.all(distance >= distance_m):
            keep.append(index)
    return result.iloc[keep].reset_index(drop=True)


def filter_candidate_sites(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    require_columns(frame, RAW_SITE_COLUMNS, "raw_candidate_sites")
    result = frame.copy()
    result["road_connected"] = _as_bool(result["road_connected"])
    result = deduplicate_by_distance(result, distance_m=20.0)

    baseline = config.get("baseline", {})
    default_capacity = float(baseline.get("default_capacity", 1000))
    if "capacity" not in result:
        result["capacity"] = default_capacity
    result["capacity"] = pd.to_numeric(result["capacity"], errors="coerce").fillna(default_capacity)

    flood_threshold = float(baseline.get("flood_safe_threshold", 0.20))
    uncertainty_quantile = float(baseline.get("uncertainty_quantile", 0.75))
    uncertainty_limit = float(result["uncertainty"].quantile(uncertainty_quantile))

    result["safe_flag"] = (
        result["road_connected"]
        & (pd.to_numeric(result["flood_prob"]) < flood_threshold)
        & (pd.to_numeric(result["uncertainty"]) <= uncertainty_limit)
    )
    result["safety_reason"] = "safe"
    result.loc[~result["road_connected"], "safety_reason"] = "no_road_connection"
    result.loc[pd.to_numeric(result["uncertainty"]) > uncertainty_limit, "safety_reason"] = (
        "high_uncertainty"
    )
    result.loc[pd.to_numeric(result["flood_prob"]) >= flood_threshold, "safety_reason"] = (
        "flood_probability"
    )
    validate_candidate_sites(result)
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("Optimization configuration must be a mapping")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--config", default="configs/optimization.yaml")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = filter_candidate_sites(read_table(args.input), load_config(args.config))
    write_table(result, args.output)
    print(f"Wrote {len(result)} candidates ({int(result.safe_flag.sum())} safe) to {args.output}")


if __name__ == "__main__":
    main()
