"""Validate the project scaffold and frozen configuration contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DIRECTORIES = [
    "configs",
    "docs",
    "src/common",
    "src/member_a",
    "src/member_b",
    "src/member_c",
    "data/raw",
    "data/interim",
    "data/processed",
    "data/external",
    "data/mock",
    "outputs/rasters",
    "outputs/vectors",
    "outputs/figures",
    "outputs/maps",
    "outputs/tables",
    "paper/sections",
    "paper/figures",
    "tests",
]

REQUIRED_FILES = [
    "README.md",
    ".gitignore",
    "requirements.txt",
    "docs/00_START_HERE.md",
    "docs/frozen_constraints.md",
    "docs/definitions.md",
    "docs/contracts.md",
    "docs/workflow_3days.md",
    "docs/handoff_checklist.md",
    "configs/dataset.yaml",
    "configs/spatial.yaml",
    "configs/model.yaml",
    "configs/assessment.yaml",
    "configs/optimization.yaml",
    "src/common/contracts.py",
    "src/member_c/risk_assessment.py",
    "src/member_c/candidate_sites.py",
    "src/member_c/facility_location.py",
    "scripts/run_member_c_pipeline.py",
    "paper/main.tex",
]


def load_yaml(relative_path: str) -> dict:
    with (ROOT / relative_path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid mapping: {relative_path}")
    return data


def main() -> int:
    problems: list[str] = []

    for relative in REQUIRED_DIRECTORIES:
        if not (ROOT / relative).is_dir():
            problems.append(f"Missing directory: {relative}")

    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            problems.append(f"Missing file: {relative}")

    dataset = load_yaml("configs/dataset.yaml")
    spatial = load_yaml("configs/spatial.yaml")
    model = load_yaml("configs/model.yaml")

    if dataset.get("provided_dataset", {}).get("sample_count") != 1000:
        problems.append("Dataset sample_count must remain 1000.")
    if dataset.get("sar", {}).get("polarization") != "HH":
        problems.append("Provided SAR polarization must remain HH.")
    if model.get("input_channels") != 5:
        problems.append("Baseline model input_channels must remain 5.")
    if spatial.get("hengzhou", {}).get("assessment_grid_size_m") != 1000:
        problems.append("Assessment grid must remain 1000 m unless change log is updated.")

    if problems:
        print("PROJECT VALIDATION FAILED")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print("PROJECT VALIDATION PASSED")
    print(f"Root: {ROOT}")
    print("Remember: provisional Hengzhou CRS and unresolved thresholds still require confirmation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
