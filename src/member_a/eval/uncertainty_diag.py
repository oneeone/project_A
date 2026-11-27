"""不确定性诊断：三种子方差是否真的"标出了错"。

只读已落盘的 test_probs.npz，不重新推理。
分箱策略：先按"三seed是否完全一致（方差=0）"分组，再在方差>0 的像素上做十分位，
避免大量 0 值把分位点压塌。

输出：
    outputs/tables/uncertainty_error_bins.csv   方差>0 像素的十分位误差率
    outputs/tables/uncertainty_diag.json        汇总指标（进论文与 Q2）
运行：python -m src.member_a.eval.uncertainty_diag
"""

from __future__ import annotations

import csv
import json

import numpy as np

from src.member_a.common import ROOT, list_patches, load_paths, read_label


def main() -> int:
    with open(ROOT / "outputs" / "tables" / "unet_eval_summary.json", encoding="utf-8") as f:
        summary = json.load(f)
    thr = float(summary["threshold_calibrated"])

    npz = np.load(ROOT / "成员A产出" / "04_栅格结果" / "unet_probs_v01" / "test_probs.npz")
    pid_arr = npz["patch_id"]
    prob = npz["prob_cal"]
    unc = npz["unc"]

    patches = {p["patch_id"]: p for p in list_patches(load_paths())}

    # 逐像素累积（跨 patch 累加，不做 patch 平均）
    us, errs = [], []
    tp_unc, fp_unc, fn_unc, tn_unc = [], [], [], []
    n_pos = n_neg = 0
    for i, pid in enumerate(pid_arr):
        lab = read_label(patches[int(pid)]["label"])
        v = lab != 255
        p = prob[i][v].astype(np.float32)
        u = unc[i][v].astype(np.float32)
        y = lab[v] == 1
        pred = p >= thr
        e = pred != y

        us.append(u)
        errs.append(e)

        tp_unc.append(u[pred & y])
        fp_unc.append(u[pred & ~y])
        fn_unc.append(u[~pred & y])
        tn_unc.append(u[~pred & ~y])
        n_pos += int(y.sum())
        n_neg += int((~y).sum())

    u_all = np.concatenate(us)
    e_all = np.concatenate(errs)

    def cat(lst):
        return np.concatenate(lst) if lst else np.zeros(0, dtype=np.float32)

    u_tp, u_fp, u_fn, u_tn = cat(tp_unc), cat(fp_unc), cat(fn_unc), cat(tn_unc)
    n_tp, n_fp, n_fn, n_tn = u_tp.size, u_fp.size, u_fn.size, u_tn.size

    m0 = u_all == 0
    print(f"阈值 {thr:.2f}，test 像素 {u_all.size:,}（正 {n_pos:,} / 负 {n_neg:,}）")
    print(f"不确定性 均值 {u_all.mean():.4f}  p99 {np.percentile(u_all, 99):.4f}  最大 {u_all.max():.4f}")
    print(f"  方差=0（三种子完全一致） {m0.sum():,} px（{m0.mean() * 100:.1f}%），误差率 {e_all[m0].mean() * 100:.2f}%")
    print(f"  方差>0                    {(~m0).sum():,} px（{(~m0).mean() * 100:.1f}%），误差率 {e_all[~m0].mean() * 100:.2f}%")
    print(f"  错判像素均值 {u_all[e_all].mean():.4f} vs 正确像素 {u_all[~e_all].mean():.4f}")
    print(f"  TP {u_tp.mean():.4f}(n={n_tp:,}) | FP {u_fp.mean():.4f}(n={n_fp:,}) | "
          f"FN {u_fn.mean():.4f}(n={n_fn:,}) | TN {u_tn.mean():.4f}(n={n_tn:,})")

    # 方差>0 的像素上做十分位
    up, ep = u_all[~m0], e_all[~m0]
    qs = np.quantile(up, np.linspace(0, 1, 11))
    rows = []
    for i in range(10):
        lo, hi = qs[i], qs[i + 1]
        sel = (up >= lo) & (up <= hi if i == 9 else up < hi)
        if sel.sum() == 0:
            continue
        rows.append({
            "decile": len(rows) + 1,
            "unc_lo": round(float(lo), 5),
            "unc_hi": round(float(hi), 5),
            "count": int(sel.sum()),
            "error_rate": round(float(ep[sel].mean()), 4),
        })

    out = ROOT / "outputs" / "tables"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "uncertainty_error_bins.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    lift = rows[-1]["error_rate"] / max(rows[0]["error_rate"], 1e-6)
    diag = {
        "threshold": thr,
        "n_pixels": int(u_all.size),
        "n_positive": int(n_pos),
        "unc_mean": round(float(u_all.mean()), 5),
        "unc_p99": round(float(np.percentile(u_all, 99)), 5),
        "unc_max": round(float(u_all.max()), 5),
        "frac_zero_unc": round(float(m0.mean()), 4),
        "error_rate_zero_unc": round(float(e_all[m0].mean()), 4),
        "error_rate_positive_unc": round(float(e_all[~m0].mean()), 4),
        "unc_mean_wrong": round(float(u_all[e_all].mean()), 5),
        "unc_mean_correct": round(float(u_all[~e_all].mean()), 5),
        "unc_ratio_wrong_over_correct": round(
            float(u_all[e_all].mean()) / max(float(u_all[~e_all].mean()), 1e-9), 2),
        "error_rate_first_decile": rows[0]["error_rate"],
        "error_rate_last_decile": rows[-1]["error_rate"],
        "error_rate_lift_top_vs_bottom": round(float(lift), 2),
        "unc_by_confusion": {
            "tp": round(float(u_tp.mean()), 5),
            "fp": round(float(u_fp.mean()), 5),
            "fn": round(float(u_fn.mean()), 5),
            "tn": round(float(u_tn.mean()), 5),
            "n_tp": n_tp, "n_fp": n_fp, "n_fn": n_fn, "n_tn": n_tn,
        },
    }
    with open(out / "uncertainty_diag.json", "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=1)

    print("\n方差>0 像素的十分位（误差率单调上升即为可用）:")
    for r in rows:
        print(f"  第{r['decile']:>2}十分位 [{r['unc_lo']:.4f},{r['unc_hi']:.4f}] "
              f"n={r['count']:>8,} 误差率 {r['error_rate'] * 100:5.2f}%")
    print(f"\n最高/最低十分位 误差率倍数 {lift:.2f}；错判/正确 不确定性比 "
          f"{diag['unc_ratio_wrong_over_correct']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
