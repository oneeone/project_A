import unittest

import pandas as pd

from src.member_c.candidate_sites import filter_candidate_sites
from src.member_c.facility_location import solve_facility_location
from src.member_c.risk_assessment import calculate_risk


def assessment_config() -> dict:
    return {
        "risk": {
            "hazard_weights": {
                "flood_area": 0.35,
                "flood_ratio": 0.35,
                "intensity": 0.20,
                "mean_probability": 0.10,
            },
            "baseline_weights": {
                "population": 0.45,
                "buildings": 0.20,
                "roads": 0.15,
                "facilities": 0.20,
            },
            "accessibility_weights": {
                "travel_time": 0.50,
                "inverse_road_density": 0.30,
                "slope": 0.20,
            },
            "accessibility_penalty": 0.5,
            "demand_risk_weight": 0.5,
        }
    }


def base_grid() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "grid_id": "G1",
                "center_x": 0.0,
                "center_y": 0.0,
                "grid_area_km2": 1.0,
                "flood_area_km2": 0.1,
                "flood_ratio": 0.1,
                "mean_flood_prob": 0.1,
                "mean_uncertainty": 0.02,
                "intensity_score": 0.1,
                "intensity_class": "general",
                "intensity_confidence": 0.9,
                "data_status": "mock",
                "total_population": 100.0,
                "total_buildings": 20.0,
                "building_area_km2": 0.02,
                "total_road_km": 3.0,
                "total_facilities": 1.0,
                "nearest_safe_facility_time_min": 10.0,
                "road_density_km_per_km2": 3.0,
                "mean_slope_degree": 1.0,
            },
            {
                "grid_id": "G2",
                "center_x": 1000.0,
                "center_y": 0.0,
                "grid_area_km2": 1.0,
                "flood_area_km2": 0.8,
                "flood_ratio": 0.8,
                "mean_flood_prob": 0.8,
                "mean_uncertainty": 0.05,
                "intensity_score": 0.8,
                "intensity_class": "extremely_strong",
                "intensity_confidence": 0.9,
                "data_status": "mock",
                "total_population": 200.0,
                "total_buildings": 40.0,
                "building_area_km2": 0.04,
                "total_road_km": 4.0,
                "total_facilities": 2.0,
                "nearest_safe_facility_time_min": 30.0,
                "road_density_km_per_km2": 2.0,
                "mean_slope_degree": 5.0,
            },
        ]
    )


class RiskTests(unittest.TestCase):
    def test_risk_is_bounded_and_high_flood_grid_ranks_higher(self) -> None:
        result = calculate_risk(base_grid(), assessment_config())
        self.assertTrue(result["risk_score"].between(0, 1).all())
        self.assertGreater(
            result.set_index("grid_id").loc["G2", "risk_score"],
            result.set_index("grid_id").loc["G1", "risk_score"],
        )
        self.assertTrue((result["demand_score"] >= 0).all())


class CandidateTests(unittest.TestCase):
    def test_candidate_filter_rejects_flood_and_disconnected_sites(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "site_id": "S1",
                    "site_type": "school",
                    "x": 0.0,
                    "y": 0.0,
                    "flood_prob": 0.1,
                    "uncertainty": 0.01,
                    "elevation_m": 50,
                    "road_connected": True,
                },
                {
                    "site_id": "S2",
                    "site_type": "school",
                    "x": 100.0,
                    "y": 0.0,
                    "flood_prob": 0.3,
                    "uncertainty": 0.02,
                    "elevation_m": 50,
                    "road_connected": True,
                },
                {
                    "site_id": "S3",
                    "site_type": "school",
                    "x": 200.0,
                    "y": 0.0,
                    "flood_prob": 0.05,
                    "uncertainty": 0.03,
                    "elevation_m": 50,
                    "road_connected": False,
                },
            ]
        )
        config = {
            "baseline": {
                "default_capacity": 1000,
                "flood_safe_threshold": 0.2,
                "uncertainty_quantile": 1.0,
            }
        }
        result = filter_candidate_sites(raw, config).set_index("site_id")
        self.assertTrue(bool(result.loc["S1", "safe_flag"]))
        self.assertFalse(bool(result.loc["S2", "safe_flag"]))
        self.assertFalse(bool(result.loc["S3", "safe_flag"]))


class OptimizationTests(unittest.TestCase):
    def test_solver_respects_capacity_and_site_limit(self) -> None:
        grids = calculate_risk(base_grid(), assessment_config())
        sites = pd.DataFrame(
            [
                {
                    "site_id": "S1",
                    "site_type": "school",
                    "x": 0.0,
                    "y": 0.0,
                    "capacity": 100.0,
                    "safe_flag": True,
                    "flood_prob": 0.0,
                    "uncertainty": 0.0,
                    "elevation_m": 50.0,
                    "road_connected": True,
                },
                {
                    "site_id": "S2",
                    "site_type": "stadium",
                    "x": 1000.0,
                    "y": 0.0,
                    "capacity": 100.0,
                    "safe_flag": True,
                    "flood_prob": 0.0,
                    "uncertainty": 0.0,
                    "elevation_m": 50.0,
                    "road_connected": True,
                },
            ]
        )
        travel = pd.DataFrame(
            [
                {"grid_id": "G1", "site_id": "S1", "travel_time_min": 5, "reachable": True},
                {"grid_id": "G1", "site_id": "S2", "travel_time_min": 20, "reachable": True},
                {"grid_id": "G2", "site_id": "S1", "travel_time_min": 20, "reachable": True},
                {"grid_id": "G2", "site_id": "S2", "travel_time_min": 5, "reachable": True},
            ]
        )
        selected, assignments, metrics = solve_facility_location(
            grids,
            sites,
            travel,
            max_sites=1,
            max_travel_time_min=30,
        )
        self.assertLessEqual(int(selected["selected"].sum()), 1)
        self.assertLessEqual(float(assignments["assigned_demand"].sum()), 100.0 + 1e-6)
        self.assertLessEqual(float(metrics["coverage_ratio"]), 1.0)


if __name__ == "__main__":
    unittest.main()
