"""Writers for Member B handoff files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.common.contracts import ASSESSMENT_REQUIRED_COLUMNS


INTERNAL_COLUMNS = {
    "_row_start",
    "_row_stop",
    "_column_start",
    "_column_stop",
}


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pixel_corner(
    transform: tuple[float, float, float, float, float, float],
    row: float,
    column: float,
) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    return c + a * column + b * row, f + d * column + e * row


def write_assessment_outputs(
    table: pd.DataFrame,
    *,
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    csv_path: str | Path,
    gpkg_path: str | Path,
    layer: str = "assessment_grid",
    geometry_by_grid_id: Mapping[str, object] | None = None,
) -> None:
    """Write the authoritative GPKG and matching contract CSV."""

    missing = set(ASSESSMENT_REQUIRED_COLUMNS) - set(table.columns)
    if missing:
        raise ValueError(f"assessment table is missing columns: {sorted(missing)}")
    if not INTERNAL_COLUMNS.issubset(table.columns):
        raise ValueError("assessment table lacks internal pixel bounds for geometry")
    try:
        import geopandas as gpd
        from shapely.geometry import Polygon
    except ImportError as exc:
        raise RuntimeError("geopandas and shapely are required to write GPKG output") from exc

    if geometry_by_grid_id is not None:
        missing_geometry = [
            str(grid_id)
            for grid_id in table["grid_id"]
            if str(grid_id) not in geometry_by_grid_id
        ]
        if missing_geometry:
            raise ValueError(
                f"frozen grid geometry is missing IDs: {missing_geometry[:5]}"
            )
        geometries = [
            geometry_by_grid_id[str(grid_id)]
            for grid_id in table["grid_id"]
        ]
    else:
        geometries = []
        for _, row in table.iterrows():
            row_start = float(row["_row_start"])
            row_stop = float(row["_row_stop"])
            column_start = float(row["_column_start"])
            column_stop = float(row["_column_stop"])
            geometries.append(
                Polygon(
                    [
                        _pixel_corner(transform, row_start, column_start),
                        _pixel_corner(transform, row_start, column_stop),
                        _pixel_corner(transform, row_stop, column_stop),
                        _pixel_corner(transform, row_stop, column_start),
                    ]
                )
            )

    public_table = table.drop(columns=sorted(INTERNAL_COLUMNS))
    csv_output = Path(csv_path)
    gpkg_output = Path(gpkg_path)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    gpkg_output.parent.mkdir(parents=True, exist_ok=True)
    public_table.to_csv(csv_output, index=False, encoding="utf-8-sig")
    geodata = gpd.GeoDataFrame(public_table, geometry=geometries, crs=crs)
    geodata.to_file(gpkg_output, layer=layer, driver="GPKG")
