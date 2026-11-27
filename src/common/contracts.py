"""Stable data contracts shared by all three members."""

FLOOD_RASTER_FILES = {
    "probability": "flood_probability.tif",
    "mask": "flood_mask.tif",
    "uncertainty": "uncertainty.tif",
    "metadata": "model_metadata.json",
}

ASSESSMENT_REQUIRED_COLUMNS = [
    "grid_id",
    "center_x",
    "center_y",
    "grid_area_km2",
    "flood_area_km2",
    "flood_ratio",
    "mean_flood_prob",
    "mean_uncertainty",
    "intensity_score",
    "intensity_class",
    "intensity_confidence",
    "data_status",
]

ASSESSMENT_MEMBER_C_COLUMNS = [
    "total_population",
    "affected_population",
    "total_buildings",
    "affected_buildings",
    "building_area_km2",
    "total_road_km",
    "flooded_road_km",
    "total_facilities",
    "affected_facilities",
    "nearest_safe_facility_time_min",
    "road_density_km_per_km2",
    "mean_slope_degree",
    "accessibility_score",
    "risk_score",
    "demand_score",
]

CANDIDATE_SITE_REQUIRED_COLUMNS = [
    "site_id",
    "site_type",
    "x",
    "y",
    "capacity",
    "safe_flag",
    "flood_prob",
    "uncertainty",
    "elevation_m",
    "road_connected",
]

SERVICE_ASSIGNMENT_REQUIRED_COLUMNS = [
    "grid_id",
    "site_id",
    "travel_time_min",
    "assigned_demand",
    "covered",
]

DATA_STATUSES = {"mock", "candidate", "final"}
LABEL_VALUES = {0, 1, 255}
