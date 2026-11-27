"""Validation for the B-to-C contract and member C outputs."""

from __future__ import annotations

import pandas as pd

from src.common.contracts import (
    ASSESSMENT_REQUIRED_COLUMNS,
    CANDIDATE_SITE_REQUIRED_COLUMNS,
    DATA_STATUSES,
    SERVICE_ASSIGNMENT_REQUIRED_COLUMNS,
)
from src.member_c.utils import assert_unique, assert_unit_interval, require_columns


def validate_assessment_grid(frame: pd.DataFrame, *, allow_mock: bool = True) -> None:
    require_columns(frame, ASSESSMENT_REQUIRED_COLUMNS, "assessment_grid")
    assert_unique(frame, "grid_id", "assessment_grid")
    assert_unit_interval(
        frame,
        ["flood_ratio", "mean_flood_prob", "intensity_confidence"],
        "assessment_grid",
    )
    statuses = set(frame["data_status"].astype(str))
    if not statuses.issubset(DATA_STATUSES):
        raise ValueError(f"Unknown data_status values: {sorted(statuses - DATA_STATUSES)}")
    if not allow_mock and "mock" in statuses:
        raise ValueError("Formal processing cannot use mock assessment rows")
    for column in ["grid_area_km2", "flood_area_km2", "mean_uncertainty"]:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any() or (numeric < 0).any():
            raise ValueError(f"assessment_grid.{column} must be finite and non-negative")
    if (frame["flood_area_km2"] > frame["grid_area_km2"] + 1e-9).any():
        raise ValueError("flood_area_km2 cannot exceed grid_area_km2")


def validate_candidate_sites(frame: pd.DataFrame) -> None:
    require_columns(frame, CANDIDATE_SITE_REQUIRED_COLUMNS, "candidate_sites")
    assert_unique(frame, "site_id", "candidate_sites")
    assert_unit_interval(frame, ["flood_prob"], "candidate_sites")
    for column in ["capacity", "uncertainty", "elevation_m"]:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any():
            raise ValueError(f"candidate_sites.{column} must be numeric")
    if (pd.to_numeric(frame["capacity"]) <= 0).any():
        raise ValueError("candidate_sites.capacity must be positive")


def validate_service_assignment(frame: pd.DataFrame) -> None:
    require_columns(frame, SERVICE_ASSIGNMENT_REQUIRED_COLUMNS, "service_assignment")
    if (pd.to_numeric(frame["travel_time_min"], errors="coerce") < 0).any():
        raise ValueError("travel_time_min cannot be negative")
    if (pd.to_numeric(frame["assigned_demand"], errors="coerce") < 0).any():
        raise ValueError("assigned_demand cannot be negative")
