"""U-Net 三种子集成评估：不确定性、阈值冻结、概率校准、面积误差。

流程：
1. 载入 unet_seed{2026,2027,2028}_v01.pth
2. val/test 逐 patch 推理，概率取三种子均值，不确定性取三种子方差
3. 阈值在 val 上扫描一次后冻结
4. isotonic 校准在 val 上拟合，test 上评估，输出 Brier/ECE 与 reliability.csv
5. 概率与不确定性落盘，供出图与栅格产品使用

输出：
    outputs/tables/unet_ensemble_metrics.csv
    outputs/tables/unet_threshold_sweep.csv
    outputs/tables/calibration_reliability.csv
    成员A产出/04_栅格结果/unet_probs_v01/{split}_probs.npz
运行：python -m src.member_a.eval.eval_unet
"""

from __future__ import annotations

import csv
import json
import time

import numpy as np
import torch
from sklearn.isotonic import IsotonicRegression

import segmentation_models_pytorch as smp

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar
from src.member_a.train.train_unet import IN_CH, MEAN, STD

SEEDS = [2026, 2027, 2028]
BATCH = 8


def load_models(device: torch.device):
    models = []
    for s in SEEDS:
        ckpt_path = ROOT / "成员A产出" / "02_模型权重" / f"unet_seed{s}_v01.pth"
        ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
        m = smp.Unet(encoder_name=ckpt["encoder"], encoder_weights=None,
                     in_channels=ckpt["in_ch"], classes=1).to(device)
        m.load_state_dict(ckpt["state_dict"])
        m.eval()
        models.append(m)
        print(f"  载入 seed {s}: epoch {ckpt['epoch']}, val IoU {ckpt['val_iou']:.4f}")
    return models


@torch.no_grad()
def infer_patches(models, records: list[dict], device: torch.device) -> dict:
    out = {}
    n = len(records)
    for i in range(0, n, BATCH):
        chunk = records[i:i + BATCH]
        batch = []
        for r in chunk:
            img = np.concatenate([read_sar(r["sar"])[None], read_optical(r["optical"])], axis=0)
            x = img.astype(np.float32) / 255.0
            x = (x - MEAN) / STD
            batch.append(x)
        xb = torch.from_numpy(np.stack(batch)).to(device)
        probs = [torch.sigmoid(m(xb)).squeeze(1).cpu().numpy() for m in models]
        stack = np.stack(probs)                       # (K, B, H, W)
        mean_p = stack.mean(axis=0).astype(np.float32)
        var = stack.var(axis=0).astype(np.float32)
        for j, r in enumerate(chunk):
            out[r["patch_id"]] = (mean_p[j], var[j])
        if (i // BATCH) % 10 == 0:
            print(f"    {min(i + BATCH, n)}/{n}", flush=True)
    return out


def scores(pred_mask: np.ndarray, lab: np.ndarray, valid: np.ndarray, area: float) -> dict:
    pred = pred_mask[valid]
    pos = lab[valid] == 1
    tp = int((pred & pos).sum())
    fp = int((pred & ~pos).sum())
    fn = int((~pred & pos).sum())
    tn = int((~pred & ~pos).sum())
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    return {
        "n_pixels": int(valid.sum()), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "iou": tp / max(tp + fp + fn, 1), "f1": 2 * prec * rec / max(prec + rec, 1e-9),
        "precision": prec, "recall": rec, "oa": (tp + tn) / max(tp + fp + fn + tn, 1),
        "pred_area_km2": float(pred.sum()) * area / 1e6,
        "true_area_km2": float(pos.sum()) * area / 1e6,
        "area_rel_err": (float(pred.sum()) - float(pos.sum())) / max(float(pos.sum()), 1.0),
    }


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= 1.0)
        if m.sum() == 0:
            continue
        total += abs(p[m].mean() - y[m].mean()) * m.sum()
    return float(total / len(p))


def main() -> int:
    t0 = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with open(ROOT / "outputs" / "tables" / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        meta = json.load(f)
    split_of = {p["patch_id"]: p["split"] for p in meta["patches"]}
    region_of = {p["patch_id"]: p["region"] for p in meta["patches"]}

    patches = list_patches(load_paths())
    for p in patches:
        p["split"] = split_of[p["patch_id"]]
        p["region"] = region_of[p["patch_id"]]
    val = [p for p in patches if p["split"] == "val"]
    test = [p for p in patches if p["split"] == "test"]

    print(f"[1/5] 载入模型（{device}）...")
    models = load_models(device)

    print(f"[2/5] 推理 val({len(val)}) / test({len(test)}) ...")
    pv = infer_patches(models, val, device)
    pt = infer_patches(models, test, device)

    # 标签与面积
    for p in val + test:
        p["label"] = read_label(p["label"])
        p["valid"] = p["label"] != 255

    print("[3/5] 阈值扫描（val，概率未校准）...")
    sweep = []
    for thr in np.round(np.arange(0.20, 0.81, 0.02), 2):
        agg = {"tp": 0, "fp": 0, "fn": 0}
        for p in val:
            prob, _ = pv[p["patch_id"]]
            s = scores(prob >= thr, p["label"], p["valid"], p["pixel_area_m2"])
            for k in agg:
                agg[k] += s[k]
        iou = agg["tp"] / max(agg["tp"] + agg["fp"] + agg["fn"], 1)
        sweep.append({"threshold": float(thr), "val_iou": round(iou, 4)})
    best = max(sweep, key=lambda r: r["val_iou"])
    thr = best["threshold"]
    print(f"      最优阈值 {thr:.2f} (val IoU {best['val_iou']:.4f})")

    print("[4/5] isotonic 校准（val 拟合，test 评估）...")
    rng = np.random.default_rng(2026)
    vp = np.concatenate([pv[p["patch_id"]][0][p["valid"]].ravel() for p in val])
    vy = np.concatenate([(p["label"][p["valid"]] == 1).astype(np.float32) for p in val])
    idx = rng.choice(vp.size, min(300_000, vp.size), replace=False)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(vp[idx], vy[idx])
    vp_cal = iso.predict(vp)

    # 校准后的概率缓存（避免在阈值扫描里反复做 isotonic 查表）
    pcal = {}
    for data in (pv, pt):
        for pid, (prob, _) in data.items():
            pcal[pid] = iso.predict(prob.ravel()).reshape(prob.shape)

    test_p = np.concatenate([pt[p["patch_id"]][0][p["valid"]].ravel() for p in test])
    test_y = np.concatenate([(p["label"][p["valid"]] == 1).astype(np.float32) for p in test])
    test_p_cal = iso.predict(test_p)
    brier_raw, brier_cal = brier(test_p, test_y), brier(test_p_cal, test_y)
    ece_raw, ece_cal = ece(test_p, test_y), ece(test_p_cal, test_y)
    print(f"      Brier  {brier_raw:.4f} -> {brier_cal:.4f}")
    print(f"      ECE    {ece_raw:.4f} -> {ece_cal:.4f}")

    # 校准后再扫一次阈值（在 val 上），最终阈值取校准后空间
    sweep_cal = []
    for t in np.round(np.arange(0.20, 0.81, 0.02), 2):
        agg = {"tp": 0, "fp": 0, "fn": 0}
        for p in val:
            s = scores(pcal[p["patch_id"]] >= t, p["label"], p["valid"], p["pixel_area_m2"])
            for k in agg:
                agg[k] += s[k]
        sweep_cal.append({"threshold": float(t), "val_iou": round(agg["tp"] / max(agg["tp"] + agg["fp"] + agg["fn"], 1), 4)})
    best_cal = max(sweep_cal, key=lambda r: r["val_iou"])
    thr_cal = best_cal["threshold"]
    print(f"      校准后最优阈值 {thr_cal:.2f} (val IoU {best_cal['val_iou']:.4f})")

    print("[5/5] 指标与落盘 ...")
    out = ROOT / "outputs" / "tables"
    out.mkdir(parents=True, exist_ok=True)
    # 面积校准阈值：在 val 上让预测面积/真值面积最接近 1（与 IoU 最优阈值不同）
    area_tbl = []
    for t in np.round(np.arange(0.20, 0.81, 0.02), 2):
        pred = sum(float((pcal[p["patch_id"]] >= t).sum()) * p["pixel_area_m2"] for p in val)
        true = sum(float((p["label"] == 1).sum()) * p["pixel_area_m2"] for p in val)
        area_tbl.append({"threshold": float(t), "val_area_ratio": pred / max(true, 1e-9)})
    best_area = min(area_tbl, key=lambda r: abs(r["val_area_ratio"] - 1.0))
    thr_area = best_area["threshold"]
    print(f"      面积校准阈值 {thr_area:.2f}（val 面积比 {best_area['val_area_ratio']:.3f}）")
    with open(out / "unet_area_calibration.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(area_tbl[0]))
        w.writeheader(); w.writerows(area_tbl)

    rows = []
    for criterion, thr_use in (("iou", thr_cal), ("area", thr_area)):
        for split in ("val", "test"):
            for region in ("all", "west", "east"):
                agg = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "n": 0, "pred": 0.0, "true": 0.0}
                for p in (val if split == "val" else test):
                    if region != "all" and p["region"] != region:
                        continue
                    s = scores(pcal[p["patch_id"]] >= thr_use, p["label"], p["valid"], p["pixel_area_m2"])
                    agg["tp"] += s["tp"]; agg["fp"] += s["fp"]; agg["fn"] += s["fn"]; agg["tn"] += s["tn"]
                    agg["n"] += s["n_pixels"]; agg["pred"] += s["pred_area_km2"]; agg["true"] += s["true_area_km2"]
                prec = agg["tp"] / max(agg["tp"] + agg["fp"], 1)
                rec = agg["tp"] / max(agg["tp"] + agg["fn"], 1)
                rows.append({
                    "criterion": criterion, "split": split, "region": region,
                    "threshold_calibrated": thr_use,
                    "n_pixels": agg["n"], "iou": round(agg["tp"] / max(agg["tp"] + agg["fp"] + agg["fn"], 1), 4),
                    "f1": round(2 * prec * rec / max(prec + rec, 1e-9), 4),
                    "precision": round(prec, 4), "recall": round(rec, 4),
                    "oa": round((agg["tp"] + agg["tn"]) / max(agg["n"], 1), 4),
                    "pred_area_km2": round(agg["pred"], 3), "true_area_km2": round(agg["true"], 3),
                    "area_rel_err": round((agg["pred"] - agg["true"]) / max(agg["true"], 1e-9), 4),
                })

    with open(out / "unet_ensemble_metrics.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    with open(out / "unet_threshold_sweep.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["threshold", "val_iou_raw", "val_iou_calibrated"])
        w.writeheader()
        for a, b in zip(sweep, sweep_cal):
            w.writerow({"threshold": a["threshold"], "val_iou_raw": a["val_iou"], "val_iou_calibrated": b["val_iou"]})

    # reliability（test，校准后）
    edges = np.linspace(0, 1, 11)
    rel = []
    for i in range(10):
        m = (test_p_cal >= edges[i]) & (test_p_cal < edges[i + 1] if i < 9 else test_p_cal <= 1.0)
        if m.sum() == 0:
            continue
        rel.append({"bin": f"{edges[i]:.1f}-{edges[i + 1]:.1f}", "mean_pred": round(float(test_p_cal[m].mean()), 4),
                    "frac_pos": round(float(test_y[m].mean()), 4), "count": int(m.sum())})
    with open(out / "calibration_reliability.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rel[0]))
        w.writeheader(); w.writerows(rel)

    with open(out / "unet_eval_summary.json", "w", encoding="utf-8") as f:
        json.dump({
            "seeds": SEEDS,
            "split_version": meta["version"],
            "threshold_raw": thr,
            "threshold_calibrated": thr_cal,
            "threshold_area": thr_area,
            "val_area_ratio_at_area_threshold": round(best_area["val_area_ratio"], 4),
            "brier_test_raw": round(brier_raw, 4),
            "brier_test_calibrated": round(brier_cal, 4),
            "ece_test_raw": round(ece_raw, 4),
            "ece_test_calibrated": round(ece_cal, 4),
        }, f, ensure_ascii=False, indent=1)

    prob_dir = ROOT / "成员A产出" / "04_栅格结果" / "unet_probs_v01"
    prob_dir.mkdir(parents=True, exist_ok=True)
    for split, data in (("val", pv), ("test", pt)):
        np.savez_compressed(
            prob_dir / f"{split}_probs.npz",
            patch_id=np.array([p["patch_id"] for p in (val if split == "val" else test)]),
            prob=np.stack([data[p["patch_id"]][0] for p in (val if split == "val" else test)]).astype(np.float16),
            prob_cal=np.stack([pcal[p["patch_id"]] for p in (val if split == "val" else test)]).astype(np.float16),
            unc=np.stack([data[p["patch_id"]][1] for p in (val if split == "val" else test)]).astype(np.float16),
        )

    print("\n指标（校准阈值 %.2f）:" % thr_cal)
    for r in rows:
        print(f"  {r['criterion']:>4} {r['split']:>4}/{r['region']:<4} thr={r['threshold_calibrated']:.2f} "
              f"IoU={r['iou']:.4f} F1={r['f1']:.4f} P={r['precision']:.3f} R={r['recall']:.3f} "
              f"面积误差={r['area_rel_err'] * 100:+.1f}%")
    print(f"\n总用时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
