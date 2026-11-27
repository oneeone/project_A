"""四件套 metadata 的统一构造器。

字段集 = scripts/verify_raster_contract.py 的 15 项必填 + docs/contracts.md §2 的补充项。
所有指标从 outputs/tables/ 现取，不在代码或论文里手抄。

provided 与 hengzhou 两个场景共用本模块，差异全部通过 extras 覆盖
（region / status / crs / reference_grid / resolution_m / 覆盖统计 / note）。
"""

from __future__ import annotations

import csv
import json
import subprocess
from datetime import datetime

from src.member_a.common import ROOT

# 通道顺序必须与 train_unet.py 的 IN_CH 一致：SAR 在首位，其后是光学 4 通道
INPUT_CHANNELS = [
    "after_gf3_hh (SAR HH)",
    "before_s2 band1 (Red)",
    "before_s2 band2 (Green)",
    "before_s2 band3 (Blue)",
    "before_s2 band4 (Auxiliary Band 4，未官方确认为 NIR)",
]
UNCERTAINTY_DEF = "K=3 个训练种子的概率方差 mean_k((p_k - mean_p)^2)，上界 0.25"
SOURCE_IMAGE_DATES = "unknown：附件文件名不含成像日期，仅标注 before/after 时相"
MODEL_VERSION = "unet_resnet18_ensemble3_v01"

MANIFEST_PATH = ROOT / "outputs" / "tables" / "provided_split_manifest_v03.json"
SUMMARY_PATH = ROOT / "outputs" / "tables" / "unet_eval_summary.json"
METRICS_PATH = ROOT / "outputs" / "tables" / "unet_ensemble_metrics.csv"


def git_state() -> tuple[str, bool]:
    """返回 (短 commit, 是否有未提交改动)；不在 git 仓库时退回 unknown。"""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            text=True, encoding="utf-8", stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT,
            text=True, encoding="utf-8", stderr=subprocess.DEVNULL).strip())
        return sha or "unknown", dirty
    except Exception:
        return "unknown", True


def load_metrics() -> tuple[dict, dict, dict, str]:
    """返回 (eval summary, val/all 指标, test/all 指标, split_rule)。"""
    with open(SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)
    with open(METRICS_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        split_rule = json.load(f)["method"]

    def pick(split: str) -> dict:
        r = next(x for x in rows if x["criterion"] == "iou"
                 and x["split"] == split and x["region"] == "all")
        return {k: float(r[k]) for k in ("iou", "f1", "precision", "recall",
                                         "pred_area_km2", "true_area_km2", "area_rel_err")}

    return summary, pick("val"), pick("test"), split_rule


def build_metadata(extras: dict) -> dict:
    """生成契约字段齐全的 metadata；extras 覆盖或以默认值补足场景相关字段。"""
    summary, m_val, m_test, split_rule = load_metrics()
    sha, dirty = git_state()
    meta = {
        "model_version": MODEL_VERSION,
        "status": "candidate",
        "threshold": float(summary["threshold_calibrated"]),
        "threshold_selected_on": "val",
        "label_semantics": "newly_inundated_only",
        "crs": "EPSG:4326",
        "reference_grid": "provided_native_4326",
        "ensemble_members": len(summary["seeds"]),
        "training_seed": summary["seeds"],
        "input_channels": INPUT_CHANNELS,
        "random_seed": summary["seeds"],
        "uncertainty_definition": UNCERTAINTY_DEF,
        "source_image_dates": SOURCE_IMAGE_DATES,
        "calibration": {"method": "isotonic", "threshold": float(summary["threshold_calibrated"]),
                        "brier": summary["brier_test_calibrated"],
                        "ece": summary["ece_test_calibrated"]},
        "validation_iou": round(m_val["iou"], 4),
        "validation_dice": round(m_val["f1"], 4),
        "area_relative_error": round(m_test["area_rel_err"], 4),
        "area_relative_error_split": "test",
        "evaluation": {
            "split": "test",
            "iou": round(m_test["iou"], 4),
            "dice": round(m_test["f1"], 4),
            "f1": round(m_test["f1"], 4),
            "precision": round(m_test["precision"], 4),
            "recall": round(m_test["recall"], 4),
            "area_abs_error_km2": round(abs(m_test["pred_area_km2"] - m_test["true_area_km2"]), 3),
            "area_rel_error": round(m_test["area_rel_err"], 4),
            "val": {"iou": round(m_val["iou"], 4), "dice": round(m_val["f1"], 4),
                    "f1": round(m_val["f1"], 4), "precision": round(m_val["precision"], 4),
                    "recall": round(m_val["recall"], 4),
                    "area_rel_error": round(m_val["area_rel_err"], 4)},
            "brier_test_calibrated": summary["brier_test_calibrated"],
            "ece_test_calibrated": summary["ece_test_calibrated"],
        },
        "split_rule": split_rule,
        "git_commit": sha,
        "git_dirty": dirty,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta.update(extras)
    return meta
