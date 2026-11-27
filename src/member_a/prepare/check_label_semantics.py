"""核验标签语义：label=1 是"灾后全部水体"还是"仅新增淹没"。

判据：用灾前光学影像做对照。
- 若 label=1 区域在灾前就偏暗（水体/深水），说明标签含常驻水体
  -> label_semantics = post_event_water_including_permanent
- 若 label=1 区域在灾前亮度与背景相当甚至更高（农田/建设用地），说明标签只标新增淹没
  -> label_semantics = newly_inundated_only

同时给出 SAR 一致性检查（label=1 应显著更暗）。

输出：outputs/tables/label_semantics_check.csv + 终端结论
运行：python -m src.member_a.prepare.check_label_semantics
"""

from __future__ import annotations

import csv
import json
import time

import numpy as np

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar

NPATCH = 150          # 抽样数量
DARK_T = 60           # 灾前光学"暗水体"亮度阈值（0-255 三通道均值）
SEED = 2026


def main() -> int:
    t0 = time.time()
    paths = load_paths()
    patches = list_patches(paths)

    # 按水体占比分层抽样，覆盖全谱
    for p in patches:
        lab = read_label(p["label"])
        valid = lab != 255
        p["water_frac"] = float((lab[valid] == 1).mean()) if valid.any() else 0.0
    patches.sort(key=lambda p: p["water_frac"])
    step = max(len(patches) // NPATCH, 1)
    sample = patches[::step][:NPATCH]

    rows = []
    for p in sample:
        lab = read_label(p["label"])
        opt = read_optical(p["optical"]).astype(np.float32)
        sar = read_sar(p["sar"]).astype(np.float32)
        bright = opt[:3].mean(axis=0)

        pos = lab == 1
        neg = lab == 0
        if pos.sum() < 50 or neg.sum() < 50:
            continue

        rows.append({
            "patch_id": p["patch_id"],
            "region": p["region"] if "region" in p else "",
            "water_frac": round(p["water_frac"], 4),
            "n_pos": int(pos.sum()),
            "opt_bright_pos": round(float(bright[pos].mean()), 2),
            "opt_bright_neg": round(float(bright[neg].mean()), 2),
            "opt_dark_frac_pos": round(float((bright[pos] < DARK_T).mean()), 4),
            "opt_dark_frac_neg": round(float((bright[neg] < DARK_T).mean()), 4),
            "sar_pos": round(float(sar[pos].mean()), 2),
            "sar_neg": round(float(sar[neg].mean()), 2),
            # 灾前亮背景被淹（新增淹没）的比例
            "newly_flooded_frac": round(float((bright[pos] >= DARK_T).mean()), 4),
        })

    if not rows:
        print("没有可用样本")
        return 1

    def agg(k: str) -> float:
        return float(np.mean([r[k] for r in rows]))

    b_pos, b_neg = agg("opt_bright_pos"), agg("opt_bright_neg")
    dark_pos, dark_neg = agg("opt_dark_frac_pos"), agg("opt_dark_frac_neg")
    newly = agg("newly_flooded_frac")
    sar_pos, sar_neg = agg("sar_pos"), agg("sar_neg")

    # 判定：新增淹没像素里"灾前是亮背景"的比例
    if newly >= 0.60:
        verdict = "newly_inundated_only"
        desc = "超过六成淹没像素在灾前不是暗水体，标签只标新增淹没"
    elif newly <= 0.30:
        verdict = "post_event_water_including_permanent"
        desc = "多数淹没像素在灾前已是暗水体，标签含常驻水体"
    else:
        verdict = "mixed"
        desc = "两成到六成之间，标签可能混有常驻水体，需要按区域分别处理"

    out_dir = ROOT / "outputs" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "label_semantics_check.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    summary = {
        "verdict": verdict,
        "description": desc,
        "n_patches": len(rows),
        "dark_threshold": DARK_T,
        "opt_bright_pos_mean": round(b_pos, 2),
        "opt_bright_neg_mean": round(b_neg, 2),
        "opt_dark_frac_pos_mean": round(dark_pos, 4),
        "opt_dark_frac_neg_mean": round(dark_neg, 4),
        "newly_flooded_frac_mean": round(newly, 4),
        "sar_pos_mean": round(sar_pos, 2),
        "sar_neg_mean": round(sar_neg, 2),
        "sar_dark_ratio": round(sar_pos / max(sar_neg, 1e-6), 3),
    }
    with open(out_dir / "label_semantics_check.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(f"抽样 {len(rows)} 个 patch，用时 {time.time() - t0:.1f}s")
    print(f"  灾前光学亮度  淹没区 {b_pos:.1f}  vs  背景 {b_neg:.1f}")
    print(f"  淹没区灾前暗水体占比   {dark_pos:.3f}   (背景 {dark_neg:.3f})")
    print(f"  淹没区灾前亮背景占比   {newly:.3f}  <- 新增淹没证据")
    print(f"  SAR 亮度      淹没区 {sar_pos:.1f}  vs  背景 {sar_neg:.1f}  (比值 {summary['sar_dark_ratio']})")
    print(f"\n判定: {verdict} — {desc}")
    print(f"  {csv_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
