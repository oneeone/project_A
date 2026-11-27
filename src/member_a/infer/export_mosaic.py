"""把一个完整的测试块导出为四件套栅格产品（probability / mask / uncertainty / metadata）。

选块规则：test 中 patch 数最多的块（通常 4x4=16 个瓦片全在），保证图幅无空洞。
坐标系：EPSG:4326 原生网格，瓦片直接拼接，不做重采样（面积仍按椭球像素面积算）。

输出：outputs/rasters/provided_test_block_mock_v01/
运行：python -m src.member_a.infer.export_mosaic
"""

from __future__ import annotations

import csv
import json
import subprocess
from datetime import datetime

import numpy as np
import rasterio
from rasterio.transform import Affine

from src.member_a.common import ROOT, list_patches, load_paths, read_label

NODATA = -9999.0
TILE = 256
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


def main() -> int:
    with open(ROOT / "outputs" / "tables" / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        meta = json.load(f)
    with open(ROOT / "outputs" / "tables" / "unet_eval_summary.json", encoding="utf-8") as f:
        summary = json.load(f)
    thr = float(summary["threshold_calibrated"])

    # 指标一律从 CSV 现取，不在元数据里手抄，改模型后自动同步
    with open(ROOT / "outputs" / "tables" / "unet_ensemble_metrics.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    def metric(split: str) -> dict:
        r = next(x for x in rows
                 if x["criterion"] == "iou" and x["split"] == split and x["region"] == "all")
        return {k: float(r[k]) for k in ("iou", "f1", "precision", "recall",
                                         "pred_area_km2", "true_area_km2", "area_rel_err")}

    m_val, m_test = metric("val"), metric("test")
    git_sha, git_dirty = git_state()

    npz = np.load(ROOT / "成员A产出" / "04_栅格结果" / "unet_probs_v01" / "test_probs.npz")
    pid_arr = npz["patch_id"]
    prob_map = {int(p): npz["prob_cal"][i] for i, p in enumerate(pid_arr)}
    unc_map = {int(p): npz["unc"][i] for i, p in enumerate(pid_arr)}

    patches = {p["patch_id"]: p for p in list_patches(load_paths())}
    test = [p for p in meta["patches"] if p["split"] == "test" and p["patch_id"] in prob_map]

    by_block: dict[str, list[dict]] = {}
    for p in test:
        by_block.setdefault(p["block_id"], []).append(p)
    block_id, members = max(by_block.items(), key=lambda kv: len(kv[1]))
    print(f"选用块 {block_id}，{len(members)} 个瓦片")

    cols = [m["tile_col"] for m in members]
    rows = [m["tile_row"] for m in members]
    c0, c1, r0, r1 = min(cols), max(cols), min(rows), max(rows)
    W, H = (c1 - c0 + 1) * TILE, (r1 - r0 + 1) * TILE

    prob = np.full((H, W), NODATA, dtype=np.float32)
    unc = np.full((H, W), NODATA, dtype=np.float32)
    for m in members:
        rr = (r1 - m["tile_row"]) * TILE
        cc = (m["tile_col"] - c0) * TILE
        pid = m["patch_id"]
        prob[rr:rr + TILE, cc:cc + TILE] = prob_map[pid].astype(np.float32)
        unc[rr:rr + TILE, cc:cc + TILE] = unc_map[pid].astype(np.float32)

    mask = np.full((H, W), 255, dtype=np.uint8)
    valid = prob != NODATA
    mask[valid] = (prob[valid] >= thr).astype(np.uint8)

    # 左上角经纬度：用块内西北角瓦片的中心反推
    nw = min(members, key=lambda m: (-m["tile_row"], m["tile_col"]))
    ref = patches[nw["patch_id"]]
    dlon, dlat = ref["pixel_dlon"], ref["pixel_dlat"]
    lon_left = nw["lon"] - TILE / 2 * dlon
    lat_top = nw["lat"] + TILE / 2 * dlat
    transform = Affine(dlon, 0.0, lon_left, 0.0, -dlat, lat_top)

    out_dir = ROOT / "outputs" / "rasters" / "provided_test_block_mock_v01"
    out_dir.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", height=H, width=W, crs="EPSG:4326", transform=transform,
                compress="deflate", predictor=2, tiled=True, blockxsize=256, blockysize=256)

    paths = {
        "prob": out_dir / "provided_flood_probability_mock_v01.tif",
        "mask": out_dir / "provided_flood_mask_mock_v01.tif",
        "unc": out_dir / "provided_flood_uncertainty_mock_v01.tif",
    }
    with rasterio.open(paths["prob"], "w", dtype="float32", nodata=NODATA, count=1, **prof) as dst:
        dst.write(prob, 1)
    with rasterio.open(paths["mask"], "w", dtype="uint8", nodata=255, count=1, **prof) as dst:
        dst.write(mask, 1)
    with rasterio.open(paths["unc"], "w", dtype="float32", nodata=NODATA, count=1, **prof) as dst:
        dst.write(unc, 1)

    valid_px = int(valid.sum())
    pred_px = int((mask == 1).sum())
    pix_area = float(np.median([patches[m["patch_id"]]["pixel_area_m2"] for m in members]))
    meta_out = {
        "model_version": "unet_resnet18_ensemble3_v01",
        "status": "mock",
        "region": block_id,
        "threshold": thr,
        "threshold_selected_on": "val",
        "label_semantics": "newly_inundated_only",
        "crs": "EPSG:4326",
        "resolution_m": round(float(np.sqrt(pix_area)), 2),
        "reference_grid": "provided_native_4326",
        "ensemble_members": len(summary["seeds"]),
        "training_seed": summary["seeds"],
        "input_channels": INPUT_CHANNELS,
        "random_seed": summary["seeds"],
        "uncertainty_definition": UNCERTAINTY_DEF,
        "source_image_dates": SOURCE_IMAGE_DATES,
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
        "calibration": {"method": "isotonic", "brier": summary["brier_test_calibrated"],
                        "ece": summary["ece_test_calibrated"]},
        "validation_iou": round(m_val["iou"], 4),
        "validation_dice": round(m_val["f1"], 4),
        "area_relative_error": round(m_test["area_rel_err"], 4),
        "area_relative_error_split": "test",
        "split_rule": meta["method"],
        "git_commit": git_sha,
        "git_dirty": git_dirty,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "note": "mock 产物，仅用于验证四件套契约与出图；block 内含该块的 test 瓦片",
        "pixel_area_m2_median": round(pix_area, 3),
        "pred_area_km2": round(pred_px * pix_area / 1e6, 4),
        "valid_pixels": valid_px,
    }
    with open(out_dir / "provided_flood_model_metadata_mock_v01.json", "w", encoding="utf-8") as f:
        json.dump(meta_out, f, ensure_ascii=False, indent=1)

    # 与真实标签对照（该块全部为 test，可入论文）
    pos = tot = 0
    for m in members:
        lab = read_label(patches[m["patch_id"]]["label"])
        ok = lab != 255
        pos += int(((lab == 1) & ok).sum())
        tot += int(ok.sum())
    true_area = pos * pix_area / 1e6
    print(f"真值淹没 {pos:,} px = {true_area:.3f} km2，预测/真值 = {meta_out['pred_area_km2'] / max(true_area, 1e-9):.3f}")

    print(f"栅格 {W}x{H}, 有效像素 {valid_px:,}, 预测淹没 {pred_px:,} px = {meta_out['pred_area_km2']:.3f} km2")
    for k, p in paths.items():
        print(f"  {k:>4}: {p.relative_to(ROOT)}  {p.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
