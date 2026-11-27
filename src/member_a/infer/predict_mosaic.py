"""整片镶嵌推理：把一片 mosaic 的全部瓦片拼成四件套栅格（candidate）。

与 export_mosaic.py 的分工：
    export_mosaic.py  只拼一个 4x4 块，用于验证接口（status=mock）
    predict_mosaic.py 拼整片镶嵌，用于 Day1 强制产出与 P4 横州推理的原型

P4 复用方式：把 list_patches() 换成 B 的影像栈读取器、把 crs/reference_grid
写进 extras，主流程与拼接逻辑不变。

运行：
    python -m src.member_a.infer.predict_mosaic --mosaic 1
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import rasterio
import torch
from rasterio.transform import Affine
from sklearn.isotonic import IsotonicRegression

from src.member_a.common import ROOT, list_patches, load_paths, read_label
from src.member_a.eval.eval_unet import infer_patches, load_models
from src.member_a.infer.contract_meta import MANIFEST_PATH, build_metadata, load_metrics

NODATA = -9999.0
TILE = 256


def fit_calibrator(raw: np.ndarray, y: np.ndarray) -> IsotonicRegression:
    """与 eval_unet.py 完全一致的拟合：rng 2026、最多 30 万像元。"""
    rng = np.random.default_rng(2026)
    idx = rng.choice(raw.size, min(300_000, raw.size), replace=False)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw[idx], y[idx])
    return iso


def main() -> int:
    ap = argparse.ArgumentParser(description="整片镶嵌推理并导出四件套")
    ap.add_argument("--mosaic", type=int, default=1, help="mosaic_id，见 provided_split_manifest_v03.json")
    ap.add_argument("--status", default="candidate", help="mock / candidate / final")
    args = ap.parse_args()

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        man = json.load(f)
    recs = [p for p in man["patches"] if p["mosaic_id"] == args.mosaic]
    if not recs:
        print(f"mosaic {args.mosaic} 无 patch")
        return 2

    paths = {p["patch_id"]: p for p in list_patches(load_paths())}
    items = []
    for r in recs:
        item = dict(paths[r["patch_id"]])
        item.update({"split": r["split"], "block_id": r["block_id"], "tile_row": r["tile_row"],
                     "tile_col": r["tile_col"]})
        item["label_arr"] = read_label(item["label"])
        item["valid"] = item["label_arr"] != 255
        items.append(item)
    n_test = sum(p["split"] == "test" for p in items)
    n_val = sum(p["split"] == "val" for p in items)
    print(f"mosaic {args.mosaic}: {len(items)} 个瓦片（train {len(items) - n_val - n_test}"
          f" / val {n_val} / test {n_test}）")

    # val/test 已由 eval_unet.py 冻结：直接复用，保证栅格与指标表逐像元一致
    cache = {}
    for split in ("val", "test"):
        z = np.load(ROOT / "成员A产出" / "04_栅格结果" / "unet_probs_v01" / f"{split}_probs.npz")
        for k, pid in enumerate(z["patch_id"]):
            cache[int(pid)] = (z["prob_cal"][k], z["unc"][k], z["prob"][k])

    todo = [p for p in items if p["patch_id"] not in cache]
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    models = load_models(device)
    print(f"[1/3] 推理 {len(todo)} 个瓦片（复用冻结 {len(items) - len(todo)} 个，{device}）...")
    fresh = infer_patches(models, todo, device) if todo else {}

    # 校准器按 eval_unet.py 的方式在全量 val(151) 上拟合，只用于给 train 瓦片定标
    val_raw, val_y = [], []
    for r in man["patches"]:
        if r["split"] != "val":
            continue
        lab = read_label(paths[r["patch_id"]]["label"])
        ok = lab != 255
        val_raw.append(cache[r["patch_id"]][2][ok])
        val_y.append((lab[ok] == 1).astype(np.float32))
    iso = fit_calibrator(np.concatenate(val_raw), np.concatenate(val_y))
    _, m_val, _, _ = load_metrics()
    print(f"[2/3] isotonic 在全量 val({len(val_y)}) 上拟合完成（val IoU 参考 {m_val['iou']:.4f}）")

    rows = [p["tile_row"] for p in items]
    cols = [p["tile_col"] for p in items]
    r0, r1, c0, c1 = min(rows), max(rows), min(cols), max(cols)
    W, H = (c1 - c0 + 1) * TILE, (r1 - r0 + 1) * TILE
    prob = np.full((H, W), NODATA, dtype=np.float32)
    unc = np.full((H, W), NODATA, dtype=np.float32)
    for p in items:
        pid = p["patch_id"]
        if pid in cache:
            cal, u = cache[pid][0], cache[pid][1]
        else:
            pr, u = fresh[pid]
            cal = iso.predict(pr.ravel()).reshape(pr.shape).astype(np.float32)
        rr = (r1 - p["tile_row"]) * TILE
        cc = (p["tile_col"] - c0) * TILE
        prob[rr:rr + TILE, cc:cc + TILE] = cal
        unc[rr:rr + TILE, cc:cc + TILE] = u

    thr = float(load_metrics()[0]["threshold_calibrated"])
    mask = np.full((H, W), 255, dtype=np.uint8)
    valid = prob != NODATA
    mask[valid] = (prob[valid] >= thr).astype(np.uint8)

    nw = min(items, key=lambda p: (-p["tile_row"], p["tile_col"]))
    dlon, dlat = nw["pixel_dlon"], nw["pixel_dlat"]
    transform = Affine(dlon, 0.0, nw["lon"] - TILE / 2 * dlon, 0.0, -dlat, nw["lat"] + TILE / 2 * dlat)

    out_dir = ROOT / "outputs" / "rasters" / f"provided_mosaic{args.mosaic}_{args.status}_v01"
    out_dir.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", height=H, width=W, crs="EPSG:4326", transform=transform,
                compress="deflate", predictor=2, tiled=True, blockxsize=256, blockysize=256)
    out = {
        "prob": out_dir / f"provided_flood_probability_{args.status}_v01.tif",
        "mask": out_dir / f"provided_flood_mask_{args.status}_v01.tif",
        "unc": out_dir / f"provided_flood_uncertainty_{args.status}_v01.tif",
    }
    with rasterio.open(out["prob"], "w", dtype="float32", nodata=NODATA, count=1, **prof) as dst:
        dst.write(prob, 1)
    with rasterio.open(out["mask"], "w", dtype="uint8", nodata=255, count=1, **prof) as dst:
        dst.write(mask, 1)
    with rasterio.open(out["unc"], "w", dtype="float32", nodata=NODATA, count=1, **prof) as dst:
        dst.write(unc, 1)

    pix_area = float(np.median([p["pixel_area_m2"] for p in items]))
    pred_px = int((mask == 1).sum())
    extras = {
        "status": args.status,
        "region": f"mosaic_{args.mosaic}",
        "resolution_m": round(float(np.sqrt(pix_area)), 2),
        "coverage": {
            "mosaic_id": args.mosaic,
            "patches": len(items),
            "grid_cells": (r1 - r0 + 1) * (c1 - c0 + 1),
            "fill_ratio": round(len(items) / ((r1 - r0 + 1) * (c1 - c0 + 1)), 4),
            "splits": {s: sum(p["split"] == s for p in items) for s in ("train", "val", "test")},
        },
        "note": "整片镶嵌推理；空洞为附件中不存在的瓦片，已填 NoData。val/test 瓦片直接复用 eval_unet.py "
                "冻结的校准概率，train 瓦片用同一 isotonic 曲线（全量 val 拟合）定标。",
        "pixel_area_m2_median": round(pix_area, 3),
        "pred_area_km2": round(pred_px * pix_area / 1e6, 4),
        "valid_pixels": int(valid.sum()),
    }
    meta = build_metadata(extras)
    with open(out_dir / f"provided_flood_model_metadata_{args.status}_v01.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    # 自检：把 test 瓦片从拼好的栅格切回来，IoU 应与 unet_ensemble_metrics.csv 对齐
    tp = fp = fn = 0
    for p in items:
        if p["split"] != "test":
            continue
        rr = (r1 - p["tile_row"]) * TILE
        cc = (p["tile_col"] - c0) * TILE
        sub = mask[rr:rr + TILE, cc:cc + TILE]
        ok = p["label_arr"] != 255
        pos = p["label_arr"][ok] == 1
        pred = sub[ok] == 1
        tp += int((pred & pos).sum())
        fp += int((pred & ~pos).sum())
        fn += int((~pred & pos).sum())
    if tp + fp + fn:
        print(f"      自检：mosaic 内 test 瓦片 IoU {tp / (tp + fp + fn):.4f}"
              f"（对照 test/all {meta['evaluation']['iou']:.4f}）")

    print(f"[3/3] 栅格 {W}x{H}，有效 {int(valid.sum()):,} px，预测淹没 {pred_px:,} px = {extras['pred_area_km2']:.3f} km2")
    for k, pth in out.items():
        print(f"  {k:>4}: {pth.relative_to(ROOT)}  {pth.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
