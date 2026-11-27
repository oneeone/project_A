"""Q2 地类误差分析：光学 4 通道 KMeans(k=5) 划地类，统计融合模型分类误差。

做法（不引入外部土地覆盖产品，可复现）：
  1. 在 test 的 83 个 patch 上对光学 4 通道像素做 KMeans(k=5)，得到 5 个"影像地类"
  2. 每个像元归到最近质心，按地类统计：SAR 均值、真值淹没占比、预测淹没占比、IoU
  3. 地类按 SAR 亮度（均值）排序，识别"低后向散射（水体/裸地）"与"高后向散射（建成区）"

用法：
    python -m src.member_a.eval.make_landcover_error
产物：
    outputs/tables/landcover_error_candidate_v01.csv
    outputs/tables/landcover_error_summary.json
"""

from __future__ import annotations

import csv
import json

import numpy as np
from sklearn.cluster import KMeans

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar

OUT = ROOT / "outputs" / "tables"
PROB_DIR = ROOT / "成员A产出" / "04_栅格结果" / "fusion_probs_v01"
K = 5
RNG = 2026


def main() -> int:
    with open(OUT / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        split_of = {p["patch_id"]: p["split"] for p in json.load(f)["patches"]}
    with open(OUT / "fusion_eval_summary.json", encoding="utf-8") as f:
        thr = float(json.load(f)["detail"]["dual"]["threshold_val_iou"])

    test = [p for p in list_patches(load_paths()) if split_of[p["patch_id"]] == "test"]
    z = np.load(PROB_DIR / "test_probs_dual.npz")
    prob = {int(pid): z["prob"][i] for i, pid in enumerate(z["patch_id"])}
    print(f"test {len(test)} 个 patch，判定阈值 {thr:.2f}")

    # 1) 采样像素拟合 KMeans
    rng = np.random.default_rng(RNG)
    sample = []
    for p in test:
        opt = read_optical(p["optical"]).astype(np.float32) / 255.0
        flat = opt.reshape(4, -1).T
        idx = rng.choice(flat.shape[0], 4000, replace=False)
        sample.append(flat[idx])
    X = np.concatenate(sample, axis=0)
    km = KMeans(n_clusters=K, random_state=RNG, n_init=10).fit(X)
    print(f"KMeans 拟合完成：{X.shape[0]} 个采样像素，{K} 个地类")

    # 2) 逐 patch 归类并累计
    n = np.zeros(K, dtype=np.int64)
    sar_sum = np.zeros(K, dtype=np.float64)
    tp = np.zeros(K, dtype=np.int64)
    fp = np.zeros(K, dtype=np.int64)
    fn = np.zeros(K, dtype=np.int64)
    for p in test:
        opt = read_optical(p["optical"]).astype(np.float32) / 255.0
        sar = read_sar(p["sar"])
        lab = read_label(p["label"])
        ok = lab != 255
        if ok.sum() == 0:
            continue
        flat = opt.reshape(4, -1).T[ok.ravel()]
        cl = km.predict(flat)
        pos = (lab == 1)[ok]
        pred = (prob[p["patch_id"]][ok] >= thr)
        s = sar[ok].astype(np.float32)
        for c in range(K):
            m = cl == c
            if not m.any():
                continue
            n[c] += int(m.sum())
            sar_sum[c] += float(s[m].sum())
            tp[c] += int((pred[m] & pos[m]).sum())
            fp[c] += int((pred[m] & ~pos[m]).sum())
            fn[c] += int((~pred[m] & pos[m]).sum())

    # 3) 按 SAR 亮度排序输出
    px_m2 = float(np.median([p["pixel_area_m2"] for p in test]))
    order = np.argsort(sar_sum / np.maximum(n, 1))
    rows = []
    for rank, c in enumerate(order):
        tot = max(n[c], 1)
        pr = tp[c] / max(tp[c] + fp[c], 1)
        rc = tp[c] / max(tp[c] + fn[c], 1)
        rows.append({
            "landcover_id": f"LC{rank}",
            "kmeans_cluster": int(c),
            "sar_mean": round(float(sar_sum[c] / tot), 2),
            "n_pixels": int(n[c]),
            "share_pct": round(100.0 * n[c] / max(n.sum(), 1), 2),
            "true_flood_frac": round(float((tp[c] + fn[c]) / tot), 4),
            "pred_flood_frac": round(float((tp[c] + fp[c]) / tot), 4),
            "precision": round(pr, 4),
            "recall": round(rc, 4),
            "iou": round(tp[c] / max(tp[c] + fp[c] + fn[c], 1), 4),
            "area_abs_err_km2": round(abs(fp[c] - fn[c]) * px_m2 / 1e6, 4),
        })

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "landcover_error_candidate_v01.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    worst = min(rows, key=lambda r: r["iou"])
    with open(OUT / "landcover_error_summary.json", "w", encoding="utf-8") as f:
        json.dump({"k": K, "rng": RNG, "threshold": thr, "pixel_area_m2_median": round(px_m2, 2),
                   "clusters": rows, "worst_iou_landcover": worst["landcover_id"],
                   "note": "地类由光学 4 通道 KMeans 划分，按 SAR 均值从小到大编号；"
                           "iou/召回/精准率在该地类像元内计算"},
                  f, ensure_ascii=False, indent=1)

    print("\n地类误差（按 SAR 由暗到亮）：")
    print(f"  {'地类':<5}{'SAR均值':>8}{'占比%':>8}{'真值淹没':>10}{'预测淹没':>10}{'IoU':>8}{'召回':>8}")
    for r in rows:
        print(f"  {r['landcover_id']:<5}{r['sar_mean']:>8.1f}{r['share_pct']:>8.1f}"
              f"{r['true_flood_frac']:>10.3f}{r['pred_flood_frac']:>10.3f}{r['iou']:>8.3f}{r['recall']:>8.3f}")
    print(f"\n误差最大地类：{worst['landcover_id']}（IoU {worst['iou']:.3f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
