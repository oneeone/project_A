"""Generate deterministic member C mock inputs matching the frozen contracts."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def generate(output_dir: Path, seed: int = 2026) -> None:
    rng = np.random.default_rng(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    grid_rows = []
    classes = ["general", "relatively_strong", "strong", "extremely_strong"]
    for row in range(5):
        for column in range(5):
            flood_ratio = float(np.clip(rng.beta(2, 5), 0, 1))
            probability = float(np.clip(flood_ratio + rng.normal(0.05, 0.08), 0, 1))
            intensity = float(0.6 * flood_ratio + 0.4 * probability)
            class_index = min(int(intensity * 4), 3)
            total_population = float(rng.integers(200, 900))
            total_buildings = float(rng.integers(20, 150))
            total_road = float(rng.uniform(1.0, 8.0))
            grid_rows.append(
                {
                    "grid_id": f"G{row:02d}{column:02d}",
                    "center_x": 500000.0 + column * 1000.0,
                    "center_y": 2600000.0 + row * 1000.0,
                    "grid_area_km2": 1.0,
                    "flood_area_km2": flood_ratio,
                    "flood_ratio": flood_ratio,
                    "mean_flood_prob": probability,
                    "mean_uncertainty": float(rng.uniform(0.01, 0.12)),
                    "intensity_score": intensity,
                    "intensity_class": classes[class_index],
                    "intensity_confidence": float(rng.uniform(0.65, 0.98)),
                    "data_status": "mock",
                    "total_population": total_population,
                    "total_buildings": total_buildings,
                    "building_area_km2": float(rng.uniform(0.02, 0.20)),
                    "total_road_km": total_road,
                    "total_facilities": float(rng.integers(0, 6)),
                    "nearest_safe_facility_time_min": float(rng.uniform(5, 45)),
                    "road_density_km_per_km2": total_road,
                    "mean_slope_degree": float(rng.uniform(0, 12)),
                }
            )
    grids = pd.DataFrame(grid_rows)
    grids.to_csv(output_dir / "assessment_grid_mock_v01.csv", index=False)

    site_types = ["school", "hospital", "stadium", "shelter", "public_facility"]
    site_rows = []
    for index in range(20):
        intended_safe = index < 16
        site_rows.append(
            {
                "site_id": f"S{index:03d}",
                "site_type": site_types[index % len(site_types)],
                "x": 500000.0 + float(rng.uniform(-200, 4200)),
                "y": 2600000.0 + float(rng.uniform(-200, 4200)),
                "capacity": 600.0,
                "flood_prob": float(
                    rng.uniform(0.0, 0.16) if intended_safe else rng.uniform(0.22, 0.40)
                ),
                "uncertainty": float(
                    rng.uniform(0.01, 0.08) if intended_safe else rng.uniform(0.10, 0.18)
                ),
                "elevation_m": float(rng.uniform(40, 110)),
                "road_connected": bool(intended_safe or rng.random() > 0.5),
            }
        )
    pd.DataFrame(site_rows).to_csv(output_dir / "candidate_sites_raw_mock_v01.csv", index=False)
    print(f"Wrote mock inputs to {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="data/mock/member_c")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    generate(Path(args.output_dir), seed=args.seed)


if __name__ == "__main__":
    main()
