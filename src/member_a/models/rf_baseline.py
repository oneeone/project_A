"""RF 基线：5 通道 + 纹理/灾前水体特征，按块划分训练与评估。

特征（9 维）：
    sar, R, G, B, Aux, 亮度, 灾前暗水体标志, SAR 局部均值(3x3), SAR 局部标准差(3x3)

评估：
    - val/test 上逐 patch 累计混淆矩阵（可跨 patch 累加，不能用 patch 平均 IoU 冒充分割精度）
    - 阈值在 val 上扫描后冻结，再用于 test
    - 面积用椭球像素面积换算，同时给出相对误差

输出：
    outputs/tables/rf_baseline_metrics.csv
    outputs/tables/rf_threshold_sweep.csv
    成员A产出/02_模型权重/rf_baseline_v01.joblib
运行：python -m src.member_a.models.rf_baseline
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime

import joblib
import numpy as np
from scipy.ndimage import uniform_filter
from sklearn.ensemble import RandomForestClassifier

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar

SEED = 2026
PIX_PER_PATCH = 1500
SENSORS = ["sar", "r", "g", "b", "aux", "bright", "darkwater", "sar_mean3", "sar_std3"]


def build_features(sar: np.ndarray, opt: np.ndarray) -> np.ndarray:
    """(H,W,9) float32。"""
    s = sar.astype(np.float32) / 255.0
    o = opt.astype(np.float32) / 255.0
    bright = o[:3].mean(axis=0)
    darkwater = (bright < 60.0 / 255.0).astype(np.float32)
    m3 = uniform_filter(s, size=3, mode="reflect")
    m3sq = uniform_filter(s * s, size=3, mode="reflect")
    std3 = np.sqrt(np.maximum(m3sq - m3 * m3, 0.0))
    return np.stack([s, o[0], o[1], o[2], o[3], bright, darkwater, m3, std3], axis=-1)


def load_split() -> dict:
    with open(ROOT / "outputs" / "tables" / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    t0 = time.time()
    paths = load_paths()
    patches = list_patches(paths)
    manifest = load_split()
    split_of = {p["patch_id"]: p["split"] for p in manifest["patches"]}
    region_of = {p["patch_id"]: p["region"] for p in manifest["patches"]}
    for p in patches:
        p["split"] = split_of[p["patch_id"]]
        p["region"] = region_of[p["patch_id"]]

    rng = np.random.default_rng(SEED)

    # ---------- 训练集采样 ----------
    print(f"[1/4] 采样训练像素（每 patch {PIX_PER_PATCH}）...")
    Xs, ys = [], []
    train = [p for p in patches if p["split"] == "train"]
    for p in train:
        sar = read_sar(p["sar"])
        lab = read_label(p["label"])
        feat = build_features(sar, read_optical(p["optical"])).reshape(-1, len(SENSORS))
        y = lab.reshape(-1)
        keep = y != 255
        idx = np.flatnonzero(keep)
        if idx.size > PIX_PER_PATCH:
            idx = rng.choice(idx, PIX_PER_PATCH, replace=False)
        Xs.append(feat[idx])
        ys.append(y[idx])
    X = np.concatenate(Xs).astype(np.float32)
    y = np.concatenate(ys).astype(np.uint8)
    print(f"      {X.shape[0]:,} 像素 x {X.shape[1]} 维，正样本占比 {y.mean():.3f}，用时 {time.time() - t0:.1f}s")

    # ---------- 训练 ----------
    print("[2/4] 训练 RandomForest ...")
    clf = RandomForestClassifier(
        n_estimators=120, max_depth=18, min_samples_leaf=5,
        max_features="sqrt", class_weight="balanced_subsample",
        n_jobs=-1, random_state=SEED, verbose=0,
    )
    clf.fit(X, y)
    print(f"      完成，用时 {time.time() - t0:.1f}s")
    imp = sorted(zip(SENSORS, clf.feature_importances_), key=lambda kv: -kv[1])
    print("      重要性: " + ", ".join(f"{k}={v:.3f}" for k, v in imp[:5]))

    # ---------- 评估 ----------
    def predict_proba(p: dict) -> np.ndarray:
        sar = read_sar(p["sar"])
        feat = build_features(sar, read_optical(p["optical"]))
        flat = feat.reshape(-1, len(SENSORS))
        out = np.empty(flat.shape[0], dtype=np.float32)
        step = 200_000
        for i in range(0, flat.shape[0], step):
            out[i:i + step] = clf.predict_proba(flat[i:i + step])[:, 1]
        return out.reshape(sar.shape)

    def collect(split: str) -> list[dict]:
        rows = []
        for p in patches:
            if p["split"] != split:
                continue
            lab = read_label(p["label"]).reshape(-1)
            prob = predict_proba(p).reshape(-1)
            valid = lab != 255
            rows.append({
                "patch_id": p["patch_id"], "region": p["region"],
                "lab": lab[valid].astype(np.uint8), "prob": prob[valid],
                "area_m2": p["pixel_area_m2"],
            })
        return rows

    print("[3/4] 推理 val / test ...")
    eval_rows = {"val": collect("val"), "test": collect("test")}
    print(f"      完成，用时 {time.time() - t0:.1f}s")

    def scores(rows: list[dict], thr: float) -> dict:
        tp = fp = fn = tn = 0
        pred_area = true_area = 0.0
        for r in rows:
            pred = r["prob"] >= thr
            pos = r["lab"] == 1
            tp += int((pred & pos).sum())
            fp += int((pred & ~pos).sum())
            fn += int((~pred & pos).sum())
            tn += int((~pred & ~pos).sum())
            pred_area += float(pred.sum()) * r["area_m2"]
            true_area += float(pos.sum()) * r["area_m2"]
        iou = tp / max(tp + fp + fn, 1)
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        return {
            "n_pixels": tp + fp + fn + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "iou": iou, "f1": 2 * prec * rec / max(prec + rec, 1e-9),
            "precision": prec, "recall": rec,
            "oa": (tp + tn) / max(tp + fp + fn + tn, 1),
            "pred_area_km2": pred_area / 1e6, "true_area_km2": true_area / 1e6,
            "area_rel_err": (pred_area - true_area) / max(true_area, 1e-9),
        }

    # 阈值在 val 上单点扫描后冻结
    print("[4/4] 阈值扫描 + 指标输出 ...")
    sweep = []
    for thr in np.round(np.arange(0.20, 0.81, 0.02), 2):
        s = scores(eval_rows["val"], float(thr))
        sweep.append({"threshold": float(thr), "val_iou": round(s["iou"], 4), "val_f1": round(s["f1"], 4)})
    best = max(sweep, key=lambda r: r["val_iou"])
    thr = best["threshold"]
    print(f"      val 最优阈值 {thr:.2f} (IoU {best['val_iou']:.4f})")

    out_dir = ROOT / "outputs" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "rf_threshold_sweep.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sweep[0]))
        w.writeheader()
        w.writerows(sweep)

    metrics = []
    for split in ("val", "test"):
        for region in ("all", "west", "east"):
            rows = eval_rows[split]
            if region != "all":
                rows = [r for r in rows if r["region"] == region]
            if not rows:
                continue
            s = scores(rows, thr)
            metrics.append({
                "split": split, "region": region, "threshold": thr,
                "n_patches": len(rows),
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()},
            })
    with open(out_dir / "rf_baseline_metrics.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(metrics[0]))
        w.writeheader()
        w.writerows(metrics)

    model_dir = ROOT / "成员A产出" / "02_模型权重"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "rf_baseline_v01.joblib"
    joblib.dump({"model": clf, "features": SENSORS, "threshold": thr,
                 "trained_at": datetime.now().isoformat(timespec="seconds"),
                 "split_version": manifest["version"]}, model_path)

    print("\n指标（阈值 %.2f）:" % thr)
    for m in metrics:
        print(f"  {m['split']:>4}/{m['region']:<4} n={m['n_patches']:>3}  "
              f"IoU={m['iou']:.4f}  F1={m['f1']:.4f}  P={m['precision']:.3f}  R={m['recall']:.3f}  "
              f"面积误差={m['area_rel_err'] * 100:+.1f}%")
    print(f"\n  {(out_dir / 'rf_baseline_metrics.csv').relative_to(ROOT)}")
    print(f"  {model_path.relative_to(ROOT)}")
    print(f"总用时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
