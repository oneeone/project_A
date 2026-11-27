"""合并 RF 基线与 U-Net 三种子集成的指标，生成 Q1 模型对比表。

对应《并行工作流程图与交付清单》第一天成员A强制产出：
    outputs/tables/q1_baseline_metrics_candidate_v01.csv

两张源表的阈值口径不同（RF 0.62 为 raw 概率，U-Net 0.46 为 isotonic 校准后），
因此保留 threshold 列，不做跨口径的"更优"结论。

运行：python -m src.member_a.eval.make_q1_table
"""

from __future__ import annotations

import csv

from src.member_a.common import ROOT

COLS = ["model", "split", "region", "threshold", "iou", "f1", "precision", "recall",
        "oa", "pred_area_km2", "true_area_km2", "area_rel_err"]


def main() -> int:
    tables = ROOT / "outputs" / "tables"
    rows: list[dict] = []

    with open(tables / "rf_baseline_metrics.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({"model": "rf", **{k: r[k] for k in COLS if k != "model"}})

    with open(tables / "unet_ensemble_metrics.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["criterion"] != "iou":
                continue
            row = {"model": "unet_ensemble3", "split": r["split"], "region": r["region"],
                   "threshold": r["threshold_calibrated"]}
            row.update({k: r[k] for k in COLS if k not in row})
            rows.append(row)

    out = tables / "q1_baseline_metrics_candidate_v01.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)

    print(f"写出 {out.relative_to(ROOT)}（{len(rows)} 行）")
    for r in rows:
        if r["region"] == "all":
            print(f"  {r['model']:>16} {r['split']:>4}  thr {r['threshold']}  "
                  f"IoU {r['iou']}  F1 {r['f1']}  面积相对误差 {r['area_rel_err']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
