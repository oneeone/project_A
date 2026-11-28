"""横州市正式推理：读取成员B的 6 波段影像栈，输出 A->B 四件套（candidate）。

流程：
  1. 读成员B交接的参考网格（EPSG:32649, 10 m）确定输出几何；
  2. 读影像栈前 5 波段（after SAR uint8 + 灾前 R/G/B/NIR），第 6 波段（原始 dB）只做诊断；
  3. 512x512 滑窗、stride 256、反射 padding，三种子集成取概率均值与方差；
  4. 阈值 0.36 二值化，永久水体置 0，NoData 落 -9999 / 255；
  5. 写三张 GeoTIFF + metadata，并调用 scripts/verify_raster_contract.py 自检。

用法：
    # 冒烟：用合成栈，在真实 8477x7199 网格上跑通全链路
    python -m src.member_a.infer.run_hengzhou_inference --synthetic

    # 正式：影像栈到位后
    python -m src.member_a.infer.run_hengzhou_inference \
        --stack 成员A产出/05_输入/hengzhou_p1_v01/hengzhou_stack_final_v01.tif \
        --permanent-water 成员A产出/05_输入/hengzhou_p1_v01/permanent_water_final_v01.tif

产物：
    成员A产出/04_栅格结果/hengzhou_<status>_v01/hengzhou_flood_probability_<status>_v01.tif
    成员A产出/04_栅格结果/hengzhou_<status>_v01/hengzhou_flood_mask_<status>_v01.tif
    成员A产出/04_栅格结果/hengzhou_<status>_v01/hengzhou_flood_uncertainty_<status>_v01.tif
    成员A产出/04_栅格结果/hengzhou_<status>_v01/hengzhou_flood_model_metadata_<status>_v01.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
import torch

from src.member_a.common import ROOT
from src.member_a.infer.contract_meta import UNCERTAINTY_DEF, git_state
from src.member_a.train.train_fusion import MEAN, STD, build_model, slice_input

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NODATA_F = -9999.0
NODATA_M = 255
PIXEL_AREA_M2 = 100.0          # 10 m x 10 m，投影坐标下固定值
WEIGHT_DIR = ROOT / "成员A产出" / "02_模型权重"
IN_DIR = ROOT / "成员A产出" / "05_输入"
OUT_BASE = ROOT / "成员A产出" / "04_栅格结果"
VERIFIER = ROOT / "scripts" / "verify_raster_contract.py"
# 随 fetch_b_handoff 取回的 B 自己的校验器；用它复验一遍，等于让 B 的代码判 A 的产物
B_CONTRACT = IN_DIR / "hengzhou_p0_v01" / "b_raster_contracts.py"


def load_payload(path: Path) -> dict:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    for key in ("arch", "members", "threshold", "encoder"):
        if key not in payload:
            raise SystemExit(f"权重包缺字段 {key}: {path}")
    return payload


def load_models(payload: dict, device: torch.device) -> list[torch.nn.Module]:
    models = []
    for state in payload["members"]:
        m = build_model(payload["arch"], payload["encoder"]).to(device)
        m.load_state_dict(state)
        m.eval()
        models.append(m)
    return models


def tile_starts(size: int, tile: int, stride: int) -> list[int]:
    if size <= tile:
        return [0]
    starts = list(range(0, size - tile + 1, stride))
    if starts[-1] != size - tile:
        starts.append(size - tile)
    return starts


def make_synthetic_stack(ref_path: Path, out_path: Path,
                         mask_path: Path | None = None) -> Path:
    """在参考网格上造一份 6 波段假栈：横向河道 + 一块浸滩 + 斑点噪声。

    映滩位置对准研究区像元中位数中心，洪水量级接近真实量级（约占研究区 5%）：
    整栈以 float32 存储（单一 GTiff 无法混合 dtype），前 5 波段数值仍为 0-255。
    """
    with rasterio.open(ref_path) as ref:
        crs, transform, h, w = ref.crs, ref.transform, ref.height, ref.width
    rng = np.random.default_rng(2026)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    inside = None
    cy, cx = int(0.58 * h), int(0.62 * w)
    if mask_path is not None and Path(mask_path).exists():
        with rasterio.open(mask_path) as m:
            if (m.height, m.width) == (h, w):
                inside = m.read(1) == 1
                ys, xs = np.nonzero(inside)
                cy, cx = int(np.median(ys)), int(np.median(xs))

    river = np.exp(-((yy - (0.45 * h + 90 * np.sin(xx / 700.0))) ** 2) / (2 * 55.0 ** 2))
    blob = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 420.0 ** 2))
    water = np.clip(0.9 * river + 0.75 * (blob > 0.35).astype(np.float32), 0, 1)

    sar = 120.0 - 90.0 * water + rng.normal(0, 12, (h, w)).astype(np.float32)
    sar_u8 = np.clip(sar, 0, 255).astype(np.uint8)
    rgb = np.stack([
        95.0 + 40.0 * (1 - water) + rng.normal(0, 6, (h, w)),
        110.0 + 35.0 * (1 - water) + rng.normal(0, 6, (h, w)),
        120.0 + 30.0 * (1 - water) + rng.normal(0, 6, (h, w)),
        140.0 + 25.0 * (1 - water) + rng.normal(0, 6, (h, w)),
    ]).astype(np.float32)
    db = (20.0 * np.log10(np.maximum(sar, 1.0) / 255.0) - 6.0).astype(np.float32)

    stack = np.concatenate([sar_u8[None].astype(np.float32), rgb, db[None]], axis=0)
    # 研究区外保留 0-255 值（与 B 的真实栈一致），有效性完全靠掩膜判；
    # 只研究区中心戳一个 100x100 缺测洞，用来测 NoData -> (-9999 / 255) 分支
    stack[:, cy - 50:cy + 50, cx - 50:cx + 50] = NODATA_F
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", driver="GTiff", height=h, width=w, count=6,
                       dtype="float32", crs=crs, transform=transform, nodata=NODATA_F,
                       compress="deflate", predictor=3, tiled=True,
                       blockxsize=256, blockysize=256) as dst:
        dst.write(stack)
    print(f"synthetic stack -> {out_path.relative_to(ROOT)}  ({out_path.stat().st_size/1e6:.1f} MB)")
    return out_path


@torch.no_grad()
def infer_stack(models: list, stack_u8: np.ndarray, valid: np.ndarray, arch: str,
                tile: int, stride: int, batch: int, device: torch.device,
                max_tiles: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (prob, unc, count)。重叠区按出现次数平均。"""
    _, h, w = stack_u8.shape
    rows, cols = tile_starts(h, tile, stride), tile_starts(w, tile, stride)
    hp = rows[-1] + tile
    wp = cols[-1] + tile
    padded = np.pad(stack_u8, ((0, 0), (0, hp - h), (0, wp - w)), mode="reflect")

    prob_sum = np.zeros((h, w), np.float32)
    unc_sum = np.zeros((h, w), np.float32)
    count = np.zeros((h, w), np.uint16)

    positions = [(r, c) for r in rows for c in cols]
    if max_tiles > 0:
        positions = positions[:max_tiles]
    t0 = time.time()
    for i in range(0, len(positions), batch):
        chunk = positions[i:i + batch]
        xb = np.stack([padded[:, r:r + tile, c:c + tile] for r, c in chunk]).astype(np.float32)
        x = torch.from_numpy(xb).div_(255.0).sub_(MEAN).div_(STD).to(device)
        t = slice_input(x, arch)
        ps = torch.stack([torch.sigmoid(m(t)) for m in models])  # (K,B,1,T,T)
        mean = ps.mean(0)[:, 0].cpu().numpy()
        var = ps.var(0, unbiased=False)[:, 0].cpu().numpy()
        for k, (r, c) in enumerate(chunk):
            prob_sum[r:r + tile, c:c + tile] += mean[k]
            unc_sum[r:r + tile, c:c + tile] += var[k]
            count[r:r + tile, c:c + tile] += 1
        if (i // batch) % 20 == 0:
            print(f"  tile {i + len(chunk)}/{len(positions)}  {time.time() - t0:.0f}s", flush=True)

    n = np.maximum(count, 1).astype(np.float32)
    prob = np.where(count > 0, prob_sum / n, NODATA_F).astype(np.float32)
    unc = np.where(count > 0, unc_sum / n, NODATA_F).astype(np.float32)
    prob[~valid] = NODATA_F
    unc[~valid] = NODATA_F
    print(f"  inference done in {time.time() - t0:.0f}s, tiles={len(positions)}")
    return prob, unc, count


def read_valid(stack_src, ref_path: Path, h: int, w: int) -> np.ndarray:
    """有效性 = 参考网格有效 且 影像栈首波段不是 NoData。

    成员B P0 交付的参考网格全为 -9999（空的几何外壳），此时不做参考掩膜；
    P1 影像栈到位后按栈自身的 NoData 判定。
    """
    valid = np.ones((h, w), bool)
    with rasterio.open(ref_path) as ref:
        if ref.nodata is not None:
            ref_arr = ref.read(1)
            ref_valid = ref_arr != ref.nodata
            if ref_valid.mean() < 0.01:
                print("  note: reference grid is all-nodata (P0 geometry shell), "
                      "validity taken from stack instead")
            else:
                valid &= ref_valid
    nod = stack_src.nodata
    if nod is not None:
        band1 = stack_src.read(1).astype(np.float32)
        valid &= band1 != float(nod)
        del band1
    return valid


def write_raster(path: Path, arr: np.ndarray, dtype: str, nodata, ref_path: Path) -> None:
    with rasterio.open(ref_path) as ref:
        crs, transform = ref.crs, ref.transform
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
                       compress="deflate", predictor=2 if dtype == "float32" else 1,
                       tiled=True, blockxsize=256, blockysize=256) as dst:
        dst.write(arr.astype(dtype), 1)


def b_contract_check(ref: Path, prob_path: Path, mask_path: Path,
                     unc_path: Path) -> list[str] | None:
    """用成员 B 自己的 validate_flood_rasters 复验四件套；未取到模块则返回 None。"""
    if not B_CONTRACT.exists():
        return None
    spec = importlib.util.spec_from_file_location("b_raster_contracts", B_CONTRACT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["b_raster_contracts"] = mod   # dataclass 需要模块在 sys.modules 里能查到
    spec.loader.exec_module(mod)
    return mod.validate_flood_rasters(ref, prob_path, mask_path, unc_path)


def build_meta(args, payload: dict, prob: np.ndarray, mask: np.ndarray, unc: np.ndarray,
               valid: np.ndarray, in_meta: dict) -> dict:
    sha, dirty = git_state()
    vp = valid & (prob != NODATA_F)
    pred_px = int((mask == 1).sum())
    meta = {
        "model_version": f"fusion_{payload['arch']}_{payload.get('weight_suffix', 'v02')}_ensemble3_v01",
        "status": args.status,
        "region": "hengzhou",
        "threshold": float(payload["threshold"]),
        "threshold_selected_on": "val",
        "label_semantics": "newly_inundated_only",
        "crs": "EPSG:32649",
        "resolution_m": 10.0,
        "reference_grid": Path(args.ref).name,
        "ensemble_members": len(payload["members"]),
        "training_seed": payload["seeds"],
        "input_channels": [
            "after_sar_uint8 (band1)",
            "before_red (band2)",
            "before_green (band3)",
            "before_blue (band4)",
            "before_nir_B08 (band5)",
        ],
        "uncertainty_definition": UNCERTAINTY_DEF + "；本产品按 3 种子逐像元方差输出",
        "study_mask": getattr(args, "study_mask_info", None),
        "source_image_dates": in_meta.get("source_image_dates", "pending"),
        "calibration": {"method": "none（直接使用原始概率，未做 isotonic）"},
        "evaluation": {
            "split": "test",
            "iou": payload.get("metrics", {}).get("test_iou"),
            "f1": payload.get("metrics", {}).get("test_f1"),
            "area_rel_error": payload.get("metrics", {}).get("test_area_rel_err"),
            "note": "指标来自附件测试集，横州无真值，不可等同于本产品精度",
        },
        "split_rule": "mosaic 4x4 block split (provided_split_manifest_v03)",
        "coverage": {
            "width": int(prob.shape[1]),
            "height": int(prob.shape[0]),
            "valid_pixels": int(vp.sum()),
            "valid_ratio": round(float(vp.mean()), 4),
            "pred_flood_pixels": pred_px,
            "pred_area_km2": round(pred_px * PIXEL_AREA_M2 / 1e6, 3),
            "prob_max": round(float(prob[vp].max()), 4) if vp.any() else None,
            "unc_max": round(float(unc[vp].max()), 4) if vp.any() else None,
        },
        "inputs": {
            "stack": Path(args.stack).name,
            "stack_sha256_prefix": args.stack_sha,
            "weight": Path(args.weight).name,
            "weight_sha256_prefix": args.weight_sha,
            "permanent_water": Path(args.permanent_water).name if args.permanent_water else None,
            "b_handoff_metadata": in_meta,
        },
        "git_commit": sha,
        "git_dirty": dirty,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if args.synthetic:
        meta["synthetic_input"] = "输入为合成栈，仅用于链路冒烟，数值不代表真实横州结果"
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="横州市四件套推理（融合模型）")
    ap.add_argument("--ref", default=str(IN_DIR / "hengzhou_p0_v01" / "hengzhou_reference_grid_final_v01.tif"))
    ap.add_argument("--in-metadata", default=str(IN_DIR / "hengzhou_p0_v01" / "hengzhou_input_metadata.json"))
    ap.add_argument("--stack", default="")
    ap.add_argument("--permanent-water", default="")
    ap.add_argument("--study-mask", default="auto",
                    help="研究区掩膜：auto=输入目录有则用；none=不用；或直接给路径")
    ap.add_argument("--weight", default=str(WEIGHT_DIR / "fusion_model_candidate_v01.pth"))
    ap.add_argument("--status", default="candidate", choices=["mock", "candidate", "final"])
    ap.add_argument("--version", default="v01")
    ap.add_argument("--tile", type=int, default=512)
    ap.add_argument("--stride", type=int, default=256)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--max-tiles", type=int, default=0, help=">0 时只跑前 N 个滑窗（冒烟用）")
    ap.add_argument("--synthetic", action="store_true", help="用合成栈跑通链路")
    ap.add_argument("--regen-synthetic", action="store_true",
                    help="强制重建合成栈（合成器改动后用）")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = ap.parse_args()

    t0 = time.time()
    ref = Path(args.ref)
    if not ref.exists():
        raise SystemExit(f"缺参考网格：{ref}\n先跑 python -m src.member_a.infer.fetch_b_handoff")

    # 研究区掩膜：B 已冻结“有效范围 = study_area_mask == 1”，默认自动用
    sm_path = None
    if args.study_mask == "auto":
        cand = IN_DIR / "hengzhou_p0_v01" / "study_area_mask_final_v01.tif"
        sm_path = cand if cand.exists() else None
    elif args.study_mask.lower() != "none":
        sm_path = Path(args.study_mask)
    if sm_path is not None and not sm_path.exists():
        raise SystemExit(f"研究区掩膜不存在：{sm_path}")

    if args.synthetic and not args.stack:
        synth = IN_DIR / "hengzhou_synthetic_v01" / "hengzhou_stack_synthetic_v01.tif"
        if synth.exists() and not args.regen_synthetic:
            print(f"synthetic stack reused: {synth.name} ({synth.stat().st_size/1e6:.1f} MB)")
        else:
            make_synthetic_stack(ref, synth, sm_path)
        args.stack = str(synth)
    if not args.stack:
        raise SystemExit("缺影像栈：--stack 或 --synthetic")
    stack_path = Path(args.stack)
    if not stack_path.exists():
        raise SystemExit(f"影像栈不存在：{stack_path}")

    import hashlib

    def sha(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    args.stack_sha, args.weight_sha = sha(stack_path), sha(Path(args.weight))
    payload = load_payload(Path(args.weight))
    device = torch.device("cuda" if (args.device == "auto" and torch.cuda.is_available())
                          else ("cpu" if args.device == "auto" else args.device))
    models = load_models(payload, device)
    print(f"arch={payload['arch']} seeds={payload['seeds']} thr={payload['threshold']} "
          f"models={len(models)} device={device}")

    in_meta = {}
    if Path(args.in_metadata).exists():
        in_meta = json.loads(Path(args.in_metadata).read_text(encoding="utf-8"))

    with rasterio.open(stack_path) as src:
        if src.count < 5:
            raise SystemExit(f"影像栈只有 {src.count} 波段，需要至少 5 波段")
        h, w = src.height, src.width
        with rasterio.open(ref) as ref_src:
            if (ref_src.height, ref_src.width) != (h, w):
                raise SystemExit(f"影像栈尺寸 {w}x{h} 与参考网格 {ref_src.width}x{ref_src.height} 不一致")
        stack_u8 = src.read(indexes=[1, 2, 3, 4, 5], out_dtype="uint8")
        valid = read_valid(src, ref, h, w)
        band6 = src.read(6).astype(np.float32) if src.count >= 6 and src.dtypes[5] == "float32" else None

    args.study_mask_info = None
    if sm_path is not None:
        with rasterio.open(sm_path) as sm:
            if (sm.height, sm.width) != (h, w):
                raise SystemExit(f"研究区掩膜尺寸 {sm.width}x{sm.height} 与影像栈 {w}x{h} 不一致")
            inside = sm.read(1) == 1
        valid &= inside
        args.study_mask_info = {
            "file": sm_path.name,
            "inside_px": int(inside.sum()),
            "inside_area_km2": round(float(inside.sum()) * PIXEL_AREA_M2 / 1e6, 3),
        }
        print(f"  study mask applied: {args.study_mask_info['inside_px']:,} px = "
              f"{args.study_mask_info['inside_area_km2']} km2")

    prob, unc, count = infer_stack(models, stack_u8, valid, payload["arch"],
                                   args.tile, args.stride, args.batch, device, args.max_tiles)

    thr = float(payload["threshold"])
    ok = valid & (count > 0)
    mask = np.full((h, w), NODATA_M, np.uint8)
    mask[ok] = (prob[ok] >= thr).astype(np.uint8)

    if args.permanent_water:
        with rasterio.open(args.permanent_water) as pw:
            perm = pw.read(1)
        hit = ok & (perm == 1)
        prob[hit] = 0.0
        mask[hit] = 0
        print(f"  permanent water zeroed: {int(hit.sum()):,} px")

    out_dir = OUT_BASE / f"hengzhou_{args.status}_{args.version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "prob": out_dir / f"hengzhou_flood_probability_{args.status}_{args.version}.tif",
        "mask": out_dir / f"hengzhou_flood_mask_{args.status}_{args.version}.tif",
        "unc": out_dir / f"hengzhou_flood_uncertainty_{args.status}_{args.version}.tif",
    }
    write_raster(files["prob"], prob, "float32", NODATA_F, ref)
    write_raster(files["mask"], mask, "uint8", NODATA_M, ref)
    write_raster(files["unc"], unc, "float32", NODATA_F, ref)

    meta = build_meta(args, payload, prob, mask, unc, valid, in_meta)
    if band6 is not None:
        meta["diagnostics"] = {"stack_band6_db_min": round(float(band6.min()), 2),
                               "stack_band6_db_max": round(float(band6.max()), 2)}
    meta_path = out_dir / f"hengzhou_flood_model_metadata_{args.status}_{args.version}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[out] {out_dir.relative_to(ROOT)}")
    for k, p in files.items():
        print(f"  {k:>4}: {p.name}  {p.stat().st_size/1e6:.1f} MB")
    print(f"  meta: {meta_path.name}")
    print(f"  valid {int(ok.sum()):,}/{h*w:,} ({ok.mean()*100:.1f}%), "
          f"flood {int((mask == 1).sum()):,} px = {meta['coverage']['pred_area_km2']:.3f} km2, "
          f"prob_max={meta['coverage']['prob_max']}, unc_max={meta['coverage']['unc_max']}")

    cmd = [sys.executable, str(VERIFIER), "--dir", str(out_dir), "--ref", str(ref)]
    if sm_path is not None:
        cmd += ["--study-mask", str(sm_path)]
    rc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("-" * 60)
    print((rc.stdout or "").strip())

    b_problems = b_contract_check(ref, files["prob"], files["mask"], files["unc"])
    print("-" * 60)
    if b_problems is None:
        print("[B 契约] 未取到 b_raster_contracts.py（先跑 fetch_b_handoff），本项跳过")
    elif b_problems:
        for p in b_problems:
            print(f"[B 契约] FAIL {p}")
    else:
        print("[B 契约] 通过：B 的 validate_flood_rasters 未报问题")

    meta["verification"] = {
        "a_verifier": {"script": "scripts/verify_raster_contract.py", "exit": rc.returncode},
        "b_contract": ("skipped" if b_problems is None
                       else ("pass" if not b_problems else b_problems)),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    if rc.returncode != 0:
        print(f"verify_raster_contract exit={rc.returncode}, 不通过")
        return 1
    if b_problems:
        print(f"B 契约不通过（{len(b_problems)} 项），不交付")
        return 1
    print(f"total {time.time() - t0:.0f}s, verify exit=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
