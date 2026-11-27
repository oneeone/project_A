"""Lightweight coordinate scatter maps for member C outputs."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def plot_risk_and_sites(
    grids: pd.DataFrame,
    selected_sites: pd.DataFrame,
    output_path: str | Path,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(8, 7))
    scatter = axis.scatter(
        grids["center_x"],
        grids["center_y"],
        c=grids["risk_score"],
        cmap="YlOrRd",
        s=120,
        marker="s",
        edgecolors="black",
        linewidths=0.25,
    )
    chosen = selected_sites.loc[selected_sites["selected"].astype(bool)]
    axis.scatter(
        chosen["x"],
        chosen["y"],
        marker="*",
        s=220,
        color="#1f77b4",
        edgecolors="white",
        linewidths=0.8,
        label="Selected rescue sites",
    )
    axis.set_title("Flood risk and selected rescue sites")
    axis.set_xlabel("Projected X (m)")
    axis.set_ylabel("Projected Y (m)")
    axis.set_aspect("equal", adjustable="box")
    axis.legend(loc="best")
    figure.colorbar(scatter, ax=axis, label="Risk score")
    figure.tight_layout()
    figure.savefig(output, dpi=200)
    plt.close(figure)
