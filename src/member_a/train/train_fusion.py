"""Q2 多源融合与消融训练（Day2）。

四种结构共用 resnet18 主干、同一数据划分、同一损失与调度，只改融合方式：
    dual     双分支（光学 4ch / SAR 1ch）+ 逐级通道注意力融合（本文提出）
    early    5 通道早期拼接（与 Q1 单枝 U-Net 同构，对照）
    optical  仅光学 4 通道（单模态下界）
    sar      仅 SAR 1 通道（单模态下界）

用法：
    python -m src.member_a.train.train_fusion --arch dual --seed 2026 --epochs 15
    python -m src.member_a.train.train_fusion --arch dual --seed 2026 --epochs 1 --limit 16   # 冒烟
产物：
    成员A产出/02_模型权重/fusion_{arch}_seed{seed}_v01.pth
    outputs/tables/fusion_train_history_{arch}_seed{seed}.csv
    outputs/tables/fusion_run_metrics_{arch}_seed{seed}.json
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

import segmentation_models_pytorch as smp
from segmentation_models_pytorch.base import SegmentationHead
from segmentation_models_pytorch.decoders.unet.decoder import UnetDecoder
from segmentation_models_pytorch.encoders import get_encoder

from src.member_a.common import ROOT, list_patches, load_paths, read_label, read_optical, read_sar

IN_CH = 5
MEAN = 0.5
STD = 0.25
DEC_CH = (256, 128, 64, 32, 16)


class PatchDS(Dataset):
    """把某个 split 的 patch 读进内存，统一返回 5 通道（SAR 在首位）。"""

    def __init__(self, records: list[dict], augment: bool = False) -> None:
        self.augment = augment
        self.items = []
        for r in records:
            sar = read_sar(r["sar"])
            opt = read_optical(r["optical"])
            img = np.concatenate([sar[None], opt], axis=0).astype(np.uint8)  # (5,H,W)
            lab = read_label(r["label"])
            self.items.append((img, lab))

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int):
        img, lab = self.items[i]
        x = img.astype(np.float32) / 255.0
        y = lab.astype(np.float32)
        valid = (lab != 255).astype(np.float32)
        y = np.where(lab == 255, 0.0, y)

        if self.augment:
            if np.random.rand() < 0.5:
                x, y, valid = x[:, :, ::-1], y[:, ::-1], valid[:, ::-1]
            if np.random.rand() < 0.5:
                x, y, valid = x[:, ::-1], y[::-1], valid[::-1]
            k = np.random.randint(4)
            if k:
                x, y, valid = np.rot90(x, k, (1, 2)), np.rot90(y, k), np.rot90(valid, k)
            x = np.ascontiguousarray(x)
            y = np.ascontiguousarray(y)
            valid = np.ascontiguousarray(valid)

        x = (x - MEAN) / STD
        return torch.from_numpy(x), torch.from_numpy(y), torch.from_numpy(valid)


class SE(nn.Module):
    """通道注意力：全局平均池化 → 瓶颈 → sigmoid 重标定。"""

    def __init__(self, ch: int, r: int = 8) -> None:
        super().__init__()
        h = max(ch // r, 4)
        self.fc1 = nn.Conv2d(ch, h, 1)
        self.fc2 = nn.Conv2d(h, ch, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        s = F.adaptive_avg_pool2d(x, 1)
        s = torch.sigmoid(self.fc2(F.relu(self.fc1(s), inplace=True)))
        return x * s


class Fuse(nn.Module):
    """单级融合：拼接 → 1x1 降维 → 通道注意力。"""

    def __init__(self, ch: int) -> None:
        super().__init__()
        self.conv = nn.Conv2d(2 * ch, ch, 1, bias=False)
        self.bn = nn.BatchNorm2d(ch)
        self.se = SE(ch)

    def forward(self, opt: torch.Tensor, sar: torch.Tensor) -> torch.Tensor:
        x = self.conv(torch.cat([opt, sar], dim=1))
        return self.se(F.relu(self.bn(x), inplace=True))


class DualBranchUnet(nn.Module):
    """光学/SAR 各一个编码器，五级特征逐一注意力融合后共用解码器。"""

    def __init__(self, encoder: str = "resnet18") -> None:
        super().__init__()
        self.enc_opt = get_encoder(encoder, in_channels=4, weights=None)
        self.enc_sar = get_encoder(encoder, in_channels=1, weights=None)
        self.fuse = nn.ModuleList([Fuse(c) for c in list(self.enc_opt.out_channels)[1:]])
        self.decoder = UnetDecoder(encoder_channels=self.enc_opt.out_channels, decoder_channels=DEC_CH)
        self.head = SegmentationHead(in_channels=DEC_CH[-1], out_channels=1, activation=None)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # smp 0.6 的 encoder 返回 [原始输入, s1, s2, s3, s4, s5]，第 0 项要丢掉
        f_opt = self.enc_opt(x[:, 1:])[1:]
        f_sar = self.enc_sar(x[:, :1])[1:]
        feats = [m(a, b) for m, a, b in zip(self.fuse, f_opt, f_sar)]
        # 解码器要求 features 与 encoder 同布局：第 0 项仅提供输入分辨率，通道会被丢弃
        return self.head(self.decoder([x[:, 1:], *feats]))


def build_model(arch: str, encoder: str) -> nn.Module:
    if arch == "dual":
        return DualBranchUnet(encoder)
    in_ch = {"early": 5, "optical": 4, "sar": 1}[arch]
    return smp.Unet(encoder_name=encoder, encoder_weights=None, in_channels=in_ch, classes=1)


def slice_input(x: torch.Tensor, arch: str) -> torch.Tensor:
    if arch == "optical":
        return x[:, 1:]
    if arch == "sar":
        return x[:, :1]
    return x


def dice_loss(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    prob = torch.sigmoid(logits) * valid
    target = target * valid
    inter = (prob * target).sum(dim=(1, 2))
    union = prob.sum(dim=(1, 2)) + target.sum(dim=(1, 2))
    return (1 - (2 * inter + 1) / (union + 1)).mean()


def masked_bce(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    loss = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    return (loss * valid).sum() / valid.sum().clamp_min(1.0)


@torch.no_grad()
def evaluate(model: nn.Module, loader: DataLoader, device: torch.device, arch: str) -> dict:
    model.eval()
    tp = fp = fn = 0
    for x, y, valid in loader:
        x = slice_input(x.to(device, non_blocking=True), arch)
        prob = torch.sigmoid(model(x)).squeeze(1).cpu().numpy()
        pred = prob >= 0.5
        pos = y.numpy() >= 0.5
        ok = valid.numpy() > 0.5
        tp += int((pred & pos & ok).sum())
        fp += int((pred & ~pos & ok).sum())
        fn += int((~pred & pos & ok).sum())
    iou = tp / max(tp + fp + fn, 1)
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    return {"iou": iou, "precision": prec, "recall": rec,
            "f1": 2 * prec * rec / max(prec + rec, 1e-9)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="dual", choices=["dual", "early", "optical", "sar"])
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--encoder", default="resnet18")
    ap.add_argument("--limit", type=int, default=0, help="仅用前 N 个训练 patch（冒烟用）")
    ap.add_argument("--suffix", default="v01", help="产物版本后缀，如 v01/e30")
    args = ap.parse_args()

    tag = f"{args.arch}_seed{args.seed}"
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.backends.cudnn.benchmark = True
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    with open(ROOT / "outputs" / "tables" / "provided_split_manifest_v03.json", encoding="utf-8") as f:
        split_of = {p["patch_id"]: p["split"] for p in json.load(f)["patches"]}
    patches = list_patches(load_paths())
    train_rec = [p for p in patches if split_of[p["patch_id"]] == "train"]
    val_rec = [p for p in patches if split_of[p["patch_id"]] == "val"]
    if args.limit:
        train_rec = train_rec[: args.limit]
        val_rec = val_rec[: max(args.limit // 2, 4)]
    print(f"[{tag}] train {len(train_rec)} / val {len(val_rec)} / device {device}", flush=True)

    t0 = time.time()
    ds_tr = PatchDS(train_rec, augment=True)
    ds_va = PatchDS(val_rec, augment=False)
    dl_tr = DataLoader(ds_tr, batch_size=args.batch, shuffle=True, num_workers=0, drop_last=True)
    dl_va = DataLoader(ds_va, batch_size=args.batch, shuffle=False, num_workers=0)
    print(f"[{tag}] 数据载入 {time.time() - t0:.1f}s", flush=True)

    model = build_model(args.arch, args.encoder).to(device)
    n_param = sum(p.numel() for p in model.parameters()) / 1e6
    print(f"[{tag}] {args.arch} / {args.encoder} / {n_param:.1f}M 参数", flush=True)

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-2)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr, total_steps=max(args.epochs * len(dl_tr), 1), pct_start=0.15)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")

    out_dir = ROOT / "outputs" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    hist_path = out_dir / f"fusion_train_history_{tag}_{args.suffix}.csv"
    model_dir = ROOT / "成员A产出" / "02_模型权重"
    model_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = model_dir / f"fusion_{tag}_{args.suffix}.pth"
    best_iou, best_epoch, rows = 0.0, -1, []

    for ep in range(1, args.epochs + 1):
        model.train()
        run_loss, nb = 0.0, 0
        te = time.time()
        for x, y, valid in dl_tr:
            x = slice_input(x.to(device, non_blocking=True), args.arch)
            y = y.to(device, non_blocking=True)
            valid = valid.to(device, non_blocking=True)
            opt.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                logits = model(x).squeeze(1)
                loss = 0.5 * masked_bce(logits, y, valid) + 0.5 * dice_loss(logits, y, valid)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            run_loss += loss.item()
            nb += 1

        msg = f"[{tag}] epoch {ep:>2}/{args.epochs} loss {run_loss / max(nb, 1):.4f}"
        if ep % 2 == 0 or ep == args.epochs:
            st = evaluate(model, dl_va, device, args.arch)
            msg += f"  val IoU {st['iou']:.4f}  F1 {st['f1']:.4f}  R {st['recall']:.3f}"
            rows.append({"epoch": ep, "loss": round(run_loss / max(nb, 1), 4),
                         **{k: round(v, 4) for k, v in st.items()}})
            if st["iou"] > best_iou:
                best_iou, best_epoch = st["iou"], ep
                torch.save({"state_dict": model.state_dict(), "arch": args.arch,
                            "encoder": args.encoder, "in_ch": IN_CH, "seed": args.seed,
                            "epoch": ep, "val_iou": st["iou"], "mean": MEAN, "std": STD},
                           ckpt_path)
        print(msg + f"  {time.time() - te:.1f}s", flush=True)

    with open(hist_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["epoch", "loss", "iou", "precision", "recall", "f1"])
        w.writeheader()
        w.writerows(rows)

    meta = {"arch": args.arch, "seed": args.seed, "epochs": args.epochs,
            "best_epoch": best_epoch, "best_val_iou": round(best_iou, 4),
            "params_m": round(n_param, 2), "train_patches": len(train_rec),
            "val_patches": len(val_rec), "seconds": round(time.time() - t0, 1)}
    with open(out_dir / f"fusion_run_metrics_{tag}_{args.suffix}.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n[{tag}] 最佳 val IoU {best_iou:.4f} @ epoch {best_epoch}", flush=True)
    print(f"  权重 {ckpt_path.relative_to(ROOT)}", flush=True)
    print(f"  总用时 {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
