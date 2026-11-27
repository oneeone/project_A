"""Small numerical and file helpers for member C."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def minmax(series: pd.Series, *, neutral: float = 0.0) -> pd.Series:
    """Return a finite 0-1 min-max normalization.

    Constant finite inputs receive neutral instead of producing NaNs.
    """
    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    finite = np.isfinite(numeric.to_numpy())
    if not finite.any():
        return pd.Series(neutral, index=series.index, dtype=float)
    low = float(numeric[finite].min())
    high = float(numeric[finite].max())
    result = pd.Series(neutral, index=series.index, dtype=float)
    if high > low:
        result.loc[finite] = (numeric.loc[finite] - low) / (high - low)
    return result.clip(0.0, 1.0)


def require_columns(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def assert_unique(frame: pd.DataFrame, column: str, name: str) -> None:
    if frame[column].isna().any():
        raise ValueError(f"{name}.{column} contains missing values")
    duplicated = frame[column].duplicated(keep=False)
    if duplicated.any():
        values = frame.loc[duplicated, column].astype(str).head(10).tolist()
        raise ValueError(f"{name}.{column} must be unique; duplicates include {values}")


def assert_unit_interval(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or ((values < 0) | (values > 1)).any():
            raise ValueError(f"{name}.{column} must contain finite values in [0, 1]")


def read_table(path: str | Path) -> pd.DataFrame:
    table_path = Path(path)
    suffix = table_path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(table_path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(table_path)
    raise ValueError(f"Unsupported tabular input: {table_path}")


def write_table(frame: pd.DataFrame, path: str | Path) -> None:
    table_path = Path(path)
    table_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = table_path.suffix.lower()
    if suffix == ".csv":
        frame.to_csv(table_path, index=False)
        return
    if suffix in {".parquet", ".pq"}:
        frame.to_parquet(table_path, index=False)
        return
    raise ValueError(f"Unsupported tabular output: {table_path}")
