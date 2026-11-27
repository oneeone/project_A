"""生成问题1的论文用图（读已落盘的产物，不重新推理）。

输出到 outputs/figures/：
    q1_samples.png     4 个测试 patch 的 SAR / 灾前光学 / 标签 / 预测
    q1_training.png    3 个种子的 loss 与 val IoU 曲线
    q1_threshold.png   val 上的阈值敏感性（原始 vs 校准）
    q1_reliability.png 校准可靠性图
    q1_uncertainty.png 不确定性分组与错判率
    q1_test_block.png  测试块的预测概率与二值掩膜
运行：python -m src.member_a.eval.make_figures
"""

from __future__ import annotations

import csv
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False
FIG = ROOT / "outputs" / "figures"


def fig_samples() -> None:
    npz = np.load(ROOT / "成员A产出" / "04_栅格结果" / "unet_probs_v01" / "test_probs.npz")
    pid_arr = npz["patch_id"]
    prob = npz["prob_cal"]
    patches = {p["patch_id"]: p for p in list_patches(load_paths())}

    ids = sorted(int(p) for p in pid_arr)
    fracs = []
    for pid in ids:
        lab = read_label(patches[pid]["label"])
        fracs.append((lab == 1).mean())
    order = np.argsort(fracs)
    picks = [ids[order[int(q * (len(order) - 1))]] for q in (0.15, 0.4, 0.65, 0.9)]

    fig, axes = plt.subplots(4, 4, figsize=(11, 11))
    titles = ["灾后 SAR (HH)", "灾前光学 (RGB)", "标签 (新增淹没)", "预测概率"]
    for r, pid in enumerate(picks):
        p = patches[pid]
        sar = read_sar(p["sar"])
        opt = np.transpose(read_optical(p["optical"])[:3], (1, 2, 0))
        lab = read_label(p["label"])
        k = int(np.where(pid_arr == pid)[0][0])
        pr = prob[k]
        axes[r, 0].imshow(sar, cmap="gray", vmin=0, vmax=255)
        axes[r, 1].imshow(np.clip(opt * 1.6, 0, 255).astype(np.uint8))
        axes[r, 2].imshow(lab, cmap="gray", vmin=0, vmax=1)
        im = axes[r, 3].imshow(pr, cmap="magma", vmin=0, vmax=1)
        for c in range(4):
            axes[r, c].set_xticks([])
            axes[r, c].set_yticks([])
            if r == 0:
                axes[r, c].set_title(titles[c], fontsize=11)
        axes[r, 0].set_ylabel(f"patch {pid}\n水体{(lab == 1).mean() * 100:.1f}%", fontsize=9)
    fig.tight_layout(rect=(0, 0, 0.93, 1))
    cax = fig.add_axes([0.945, 0.3, 0.012, 0.4])
    fig.colorbar(im, cax=cax, label="淹没概率")
    fig.savefig(FIG / "q1_samples.png", dpi=180)
    plt.close(fig)


def fig_training() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for seed in (2026, 2027, 2028):
        path = ROOT / "outputs" / "tables" / f"unet_train_history_seed{seed}.csv"
        if not path.exists():
            continue
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        ep = [int(r["epoch"]) for r in rows]
        axes[0].plot(ep, [float(r["loss"]) for r in rows], label=f"seed {seed}")
        axes[1].plot(ep, [float(r["iou"]) for r in rows], label=f"seed {seed}")
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("训练损失")
    axes[0].set_title("BCE + Dice 损失")
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("验证集 IoU")
    axes[1].set_title("验证集 IoU（按块划分）")
    for a in axes:
        a.grid(alpha=0.3)
        a.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "q1_training.png", dpi=180)
    plt.close(fig)


def fig_threshold() -> None:
    path = ROOT / "outputs" / "tables" / "unet_threshold_sweep.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    t = [float(r["threshold"]) for r in rows]
    fig, ax = plt.subplots(figsize=(5.2, 3.6))
    ax.plot(t, [float(r["val_iou_raw"]) for r in rows], marker="o", ms=3, label="原始概率")
    ax.plot(t, [float(r["val_iou_calibrated"]) for r in rows], marker="s", ms=3, label="isotonic 校准后")
    ax.set_xlabel("二值化阈值")
    ax.set_ylabel("验证集 IoU")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    ax.set_title("阈值敏感性（仅在验证集上选择）")
    fig.tight_layout()
    fig.savefig(FIG / "q1_threshold.png", dpi=180)
    plt.close(fig)


def fig_reliability() -> None:
    path = ROOT / "outputs" / "tables" / "calibration_reliability.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    xs = [float(r["mean_pred"]) for r in rows]
    ys = [float(r["frac_pos"]) for r in rows]
    cnt = [int(r["count"]) for r in rows]
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    ax.plot([0, 1], [0, 1], "--", color="gray", lw=1, label="理想校准")
    ax.scatter(xs, ys, s=[max(c, 1) ** 0.4 * 2 for c in cnt], zorder=3)
    ax.plot(xs, ys, lw=1, alpha=0.6)
    ax.set_xlabel("预测概率")
    ax.set_ylabel("实际淹没比例")
    ax.set_title("可靠性图（测试集，isotonic 校准后）")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / "q1_reliability.png", dpi=180)
    plt.close(fig)


def fig_uncertainty() -> None:
    bins = list(csv.DictReader(open(ROOT / "outputs" / "tables" / "uncertainty_error_bins.csv",
                                     encoding="utf-8-sig")))
    with open(ROOT / "outputs" / "tables" / "uncertainty_diag.json", encoding="utf-8") as f:
        diag = json.load(f)
    labels = ["方差=0"] + [f"D{r['decile']}" for r in bins]
    rates = [diag["error_rate_zero_unc"] * 100] + [float(r["error_rate"]) * 100 for r in bins]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    bars = ax.bar(range(len(rates)), rates, color=["#9ecae1"] + ["#3182bd"] * len(bins))
    ax.set_xticks(range(len(rates)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_xlabel("三种子方差分组（D1 最低 → D8 最高）")
    ax.set_ylabel("像素级错判率 (%)")
    ax.set_title("不确定性越高，错判越集中")
    for b, v in zip(bars, rates):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}", ha="center", va="bottom", fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "q1_uncertainty.png", dpi=180)
    plt.close(fig)


def fig_block() -> None:
    base = ROOT / "outputs" / "rasters" / "provided_test_block_mock_v01"
    with rasterio.open(base / "provided_flood_probability_mock_v01.tif") as ds:
        prob = ds.read(1)
        nod = ds.nodata
    with rasterio.open(base / "provided_flood_mask_mock_v01.tif") as ds:
        mask = ds.read(1)
    prob = np.where(prob == nod, np.nan, prob)
    mask = np.where(mask == 255, np.nan, mask)

    fig, axes = plt.subplots(1, 2, figsize=(9, 4.2))
    im = axes[0].imshow(prob, cmap="magma", vmin=0, vmax=1)
    axes[0].set_title("淹没概率")
    axes[1].imshow(mask, cmap="Blues", vmin=0, vmax=1)
    axes[1].set_title("二值掩膜（阈值取自验证集）")
    for a in axes:
        a.set_xticks([])
        a.set_yticks([])
    fig.colorbar(im, ax=axes[0], shrink=0.75, label="概率")
    fig.tight_layout()
    fig.savefig(FIG / "q1_test_block.png", dpi=180)
    plt.close(fig)


def main() -> int:
    FIG.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "outputs" / "tables" / "unet_eval_summary.json", encoding="utf-8") as f:
        s = json.load(f)
    print(f"阈值 {s['threshold_calibrated']}, Brier {s['brier_test_calibrated']}, ECE {s['ece_test_calibrated']}")
    fig_samples()
    fig_training()
    fig_threshold()
    fig_reliability()
    fig_uncertainty()
    fig_block()
    for p in sorted(FIG.glob("q1_*.png")):
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size / 1e3:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
