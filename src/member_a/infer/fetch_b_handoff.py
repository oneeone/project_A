"""从成员B分支提取交接一（B->A）输入到本地工作目录，并做几何自检。

做三件事：
  1. 用 git show 从指定 revision 取文件（不切分支、不改工作区）；
  2. 解压参考网格 zip，落盘到 成员A产出/05_输入/<stage>/；
  3. 打开栅格核对 CRS/transform/尺寸/NoData，并列出 P1 尚未交付的项。

用法：
    python -m src.member_a.infer.fetch_b_handoff
    python -m src.member_a.infer.fetch_b_handoff --rev ea4d746 --stage p0_v01

产物（全部在 git 排除目录内，不入仓库）：
    成员A产出/05_输入/hengzhou_p0_v01/hengzhou_reference_grid_final_v01.tif
    成员A产出/05_输入/hengzhou_p0_v01/hengzhou_grid_1km.json
    成员A产出/05_输入/hengzhou_p0_v01/hengzhou_input_metadata.json
    成员A产出/05_输入/hengzhou_p0_v01/b_raster_contracts.py   （B 的校验器，供离线复验）
    成员A产出/05_输入/hengzhou_p0_v01/fetch_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from src.member_a.common import ROOT

IN_DIR = ROOT / "成员A产出" / "05_输入"

# 交接一四件套在 B 分支中的路径（P0 已交前三项）
REQUIRED = {
    "reference_zip": "outputs/samples/hengzhou_reference_grid_final_v01.zip",
    "spatial_zip": "outputs/samples/hengzhou_spatial_contract_final_v01.zip",
    "grid_1km": "outputs/tables/hengzhou_grid_1km.json",
    "input_metadata": "outputs/tables/hengzhou_input_metadata.json",
}
# 随交接一并取回 B 的契约校验器，A 侧用它对自家产物做一遍独立判定
CONTRACT_SRC = "src/member_b/raster_contracts.py"
# P1 尚未交付，脚本只用于报告缺失，不报错
P1_PATTERNS = {
    "stack": ["hengzhou_stack_final_v01.tif", "hengzhou_stack_final_v01.zip"],
    "permanent_water": ["permanent_water_final_v01.tif", "permanent_water_final_v01.zip"],
}


def git(args: list[str]) -> bytes:
    return subprocess.check_output(["git", *args], cwd=str(ROOT), stderr=subprocess.PIPE)


def git_show(rev: str, path: str) -> bytes:
    return git(["show", f"{rev}:{path}"])


def tree_paths(rev: str) -> list[str]:
    out = git(["ls-tree", "-r", "--name-only", rev]).decode("utf-8", "replace")
    return [line.strip() for line in out.splitlines() if line.strip()]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="origin/member-b",
                    help="来源 revision，默认 origin/member-b")
    ap.add_argument("--stage", default="p0_v01",
                    help="本地目录后缀，默认 p0_v01")
    args = ap.parse_args()

    rev_sha = git(["rev-parse", "--short", args.rev]).decode().strip()
    out_dir = IN_DIR / f"hengzhou_{args.stage}"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = tree_paths(args.rev)
    written: dict[str, dict] = {}
    missing: list[str] = []

    for key, src in REQUIRED.items():
        if src not in paths:
            missing.append(f"{key}: {src}")
            continue
        data = git_show(args.rev, src)
        if src.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                zf.extractall(out_dir)
            for name in names:
                p = out_dir / name
                if p.is_file():
                    written[name] = {"bytes": p.stat().st_size,
                                     "sha256": sha256(p.read_bytes())}
        else:
            p = out_dir / Path(src).name
            p.write_bytes(data)
            written[p.name] = {"bytes": len(data), "sha256": sha256(data)}

    # B 的校验器（存在才取；取不到不报错，只是少一道复验）
    b_contract = None
    if CONTRACT_SRC in paths:
        data = git_show(args.rev, CONTRACT_SRC)
        p = out_dir / "b_raster_contracts.py"
        p.write_bytes(data)
        written[p.name] = {"bytes": len(data), "sha256": sha256(data),
                           "source": CONTRACT_SRC}
        b_contract = p.name

    # 可选：P1 影像栈与永久水体
    p1_status: dict[str, str] = {}
    for key, candidates in P1_PATTERNS.items():
        hit = next((c for c in candidates if any(p.endswith(c) for p in paths)), None)
        p1_status[key] = hit if hit else "missing"

    # 几何自检
    ref_tif = out_dir / "hengzhou_reference_grid_final_v01.tif"
    geometry: dict = {}
    if ref_tif.exists():
        import numpy as np
        import rasterio

        with rasterio.open(ref_tif) as ds:
            arr = ds.read(1)
            geometry = {
                "crs": str(ds.crs),
                "transform": [float(v) for v in ds.transform[:6]],
                "width": ds.width,
                "height": ds.height,
                "dtype": ds.dtypes[0],
                "nodata": ds.nodata,
                "unique_values": int(np.unique(arr).size),
            }

    meta_path = out_dir / "hengzhou_input_metadata.json"
    meta_matches = None
    if meta_path.exists() and geometry:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta_matches = (
            meta.get("crs") == geometry["crs"]
            and int(meta.get("width", -1)) == geometry["width"]
            and int(meta.get("height", -1)) == geometry["height"]
            and all(abs(a - b) <= 1e-9 for a, b in
                    zip(meta.get("transform", []), geometry["transform"]))
        )

    grid_info = {}
    grid_path = out_dir / "hengzhou_grid_1km.json"
    if grid_path.exists():
        grid = json.loads(grid_path.read_text(encoding="utf-8"))
        cells = grid.get("cells", grid)
        ids = [c["grid_id"] for c in cells]
        grid_info = {
            "cell_count": len(cells),
            "unique_ids": len(set(ids)) == len(ids),
            "ids_continuous": set(ids) == {f"G{i:06d}" for i in range(1, len(cells) + 1)},
            "has_pixel_windows": all(
                {"row_start", "row_stop", "column_start", "column_stop"} <= set(c)
                for c in cells),
            "origin_m": grid.get("origin_m") if isinstance(grid, dict) else None,
        }

    # 研究区掩膜：B 已冻结“有效范围必须读 study_area_mask == 1”，A 侧据此定有效足迹
    mask_info: dict = {}
    sm_path = out_dir / "study_area_mask_final_v01.tif"
    if sm_path.exists() and geometry:
        import numpy as np
        import rasterio

        with rasterio.open(sm_path) as ds:
            arr = ds.read(1)
            uniq, counts = np.unique(arr, return_counts=True)
            inside = int(counts[uniq == 1].sum())
            mask_info = {
                "dtype": ds.dtypes[0],
                "nodata": ds.nodata,
                "matches_reference": (
                    str(ds.crs) == geometry["crs"]
                    and (ds.width, ds.height) == (geometry["width"], geometry["height"])
                    and all(abs(a - b) <= 1e-9 for a, b in
                            zip(ds.transform[:6], geometry["transform"]))),
                "unique": {int(u): int(c) for u, c in zip(uniq, counts)},
                "inside_px": inside,
                "inside_area_km2": round(inside * 100.0 / 1e6, 3),
            }

    manifest = {
        "source_rev": args.rev,
        "source_sha": rev_sha,
        "out_dir": str(out_dir.relative_to(ROOT)),
        "files": written,
        "geometry": geometry,
        "metadata_matches_reference": meta_matches,
        "grid_1km": grid_info,
        "study_area_mask": mask_info,
        "b_contract_module": b_contract,
        "p1_pending": p1_status,
    }
    (out_dir / "fetch_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"rev={rev_sha} -> {manifest['out_dir']}")
    for name, info in written.items():
        print(f"  ok  {name}  {info['bytes']} bytes")
    if missing:
        print("  missing required:")
        for m in missing:
            print(f"    - {m}")
    if geometry:
        print(f"  crs={geometry['crs']} size={geometry['width']}x{geometry['height']} "
              f"nodata={geometry['nodata']} unique={geometry['unique_values']}")
        print(f"  metadata_matches_reference={meta_matches}")
    if grid_info:
        print(f"  grid cells={grid_info['cell_count']} unique_ids={grid_info['unique_ids']} "
              f"ids_continuous={grid_info['ids_continuous']} "
              f"pixel_windows={grid_info['has_pixel_windows']}")
    if mask_info:
        print(f"  study mask matches={mask_info['matches_reference']} "
              f"values={mask_info['unique']} inside={mask_info['inside_area_km2']} km2")
    print(f"  b_contract={b_contract}")
    print(f"  p1: stack={p1_status['stack']} permanent_water={p1_status['permanent_water']}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
