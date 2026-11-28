#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A -> B 栅格接口校验器  (对应冻结项②, 见 docs/contracts_A_to_B_v01.md)

用法:
    python verify_raster_contract.py --dir <A的输出目录> --ref <参考栅格.tif>
    python verify_raster_contract.py --selftest

退出码:
    0 = 通过（可以发给 B）
    1 = 不通过（禁止交付）
    2 = 环境/参数错误

依赖: rasterio, numpy
"""

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_origin

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FLOAT_NODATA = -9999.0
MASK_NODATA = 255
UNC_MAX = 0.25
ALIGN_ATOL = 1e-6

REQUIRED_META = [
    "model_version", "status", "region", "threshold", "threshold_selected_on",
    "label_semantics", "crs", "resolution_m", "reference_grid",
    "ensemble_members", "training_seed", "evaluation", "calibration",
    "split_rule", "created_at",
]
LEGAL_LABEL_SEMANTICS = {
    "post_event_water_including_permanent",
    "newly_inundated_only",
}


def _find(d, pattern):
    hits = sorted(glob.glob(os.path.join(d, pattern)))
    return hits[-1] if hits else None


def _grid_check(src, ref_crs, ref_t, ref_w, ref_h, tag, fail, warn):
    if str(src.crs) != str(ref_crs):
        fail("[%s] CRS 不一致: %s != %s" % (tag, src.crs, ref_crs))
    if src.width != ref_w or src.height != ref_h:
        fail("[%s] 尺寸不一致: %dx%d != %dx%d" % (tag, src.width, src.height, ref_w, ref_h))
    if not np.allclose(tuple(src.transform)[:6], tuple(ref_t)[:6], atol=ALIGN_ATOL):
        fail("[%s] transform 不一致（原点或像元大小被改动）" % tag)
    if src.count != 1:
        fail("[%s] 必须单波段，实际 %d 波段" % (tag, src.count))


def check_dir(d, ref_path, study_mask_path=None):
    """返回 [(level, message), ...]，level ∈ {OK, WARN, FAIL}

    study_mask_path 给定后，按 B 冻结的“研究区有效范围 = study_area_mask == 1”判定：
    研究区内必须全部有效，研究区外必须全为 NoData。
    """
    res = []

    def ok(m):
        res.append(("OK", m))

    def warn(m):
        res.append(("WARN", m))

    def fail(m):
        res.append(("FAIL", m))

    try:
        with rasterio.open(ref_path) as ref:
            ref_crs, ref_t, ref_w, ref_h = ref.crs, ref.transform, ref.width, ref.height
    except Exception as e:
        fail("参考栅格无法打开: %s" % e)
        return res

    files = {
        "flood_probability": _find(d, "*flood_probability*.tif"),
        "flood_mask": _find(d, "*flood_mask*.tif"),
        "uncertainty": _find(d, "*uncertainty*.tif"),
        "metadata": _find(d, "*flood_model_metadata*.json"),
    }
    for k, v in files.items():
        if v is None:
            fail("缺少文件: %s" % k)
    if any(v is None for v in files.values()):
        return res
    ok("四个文件均已找到")

    inside = None
    if study_mask_path:
        try:
            with rasterio.open(study_mask_path) as sm:
                inside = sm.read(1) == 1
        except Exception as e:
            fail("研究区掩膜无法打开: %s" % e)

    # ---------- metadata ----------
    threshold = None
    try:
        with open(files["metadata"], encoding="utf-8") as f:
            meta = json.load(f)
        missing = [k for k in REQUIRED_META if k not in meta]
        if missing:
            fail("metadata 缺字段: %s" % ", ".join(missing))
        else:
            ok("metadata 必填字段齐全")
        threshold = meta.get("threshold")
        if not isinstance(threshold, (int, float)) or not (0.0 < float(threshold) < 1.0):
            fail("metadata.threshold 非法: %r" % threshold)
            threshold = None
        if meta.get("label_semantics") not in LEGAL_LABEL_SEMANTICS:
            fail("label_semantics 未按合同填: %r（只能是 %s）"
                 % (meta.get("label_semantics"), sorted(LEGAL_LABEL_SEMANTICS)))
        if meta.get("crs") != "EPSG:32649":
            warn("metadata.crs = %r，与冻结标准 EPSG:32649 不一致" % meta.get("crs"))
        if meta.get("status") == "final":
            ev = meta.get("evaluation") or {}
            if float(ev.get("iou", 0) or 0) <= 0:
                warn("status=final 但 evaluation.iou 为 0，疑似未填实测值")
            cal = meta.get("calibration") or {}
            if float(cal.get("brier", 0) or 0) <= 0:
                warn("status=final 但未提供校准指标（C 会用 0.2 阈值做安全判定）")
    except Exception as e:
        fail("metadata 解析失败: %s" % e)

    # ---------- 三张栅格 ----------
    arrays = {}
    for tag, path in (("flood_probability", files["flood_probability"]),
                      ("flood_mask", files["flood_mask"]),
                      ("uncertainty", files["uncertainty"])):
        try:
            with rasterio.open(path) as src:
                _grid_check(src, ref_crs, ref_t, ref_w, ref_h, tag, fail, warn)
                nod = src.nodata
                arr = src.read(1)
                arrays[tag] = (arr, nod, src.dtypes[0])
        except Exception as e:
            fail("[%s] 无法读取: %s" % (tag, e))
    if len(arrays) < 3:
        return res

    # prob
    arr, nod, dt = arrays["flood_probability"]
    if dt != "float32":
        fail("[flood_probability] dtype 应为 float32，实际 %s" % dt)
    if nod is None or abs(float(nod) - FLOAT_NODATA) > 1e-6:
        fail("[flood_probability] nodata 应为 %s，实际 %r" % (FLOAT_NODATA, nod))
    valid = arr != (FLOAT_NODATA if nod is None else nod)
    if not np.isfinite(arr[valid]).all():
        fail("[flood_probability] 存在 NaN/Inf")
    if valid.any():
        vmin, vmax = float(arr[valid].min()), float(arr[valid].max())
        if vmin < 0 or vmax > 1:
            fail("[flood_probability] 值域越界: [%.6f, %.6f]，应落在 [0,1]" % (vmin, vmax))
        else:
            ok("[flood_probability] 值域 [%.4f, %.4f]" % (vmin, vmax))
        cover = valid.mean()
        if inside is not None:
            if inside.shape != valid.shape:
                fail("[覆盖率] 研究区掩膜尺寸与概率栅格不一致")
            else:
                n_in = int(inside.sum())
                miss = int((inside & ~valid).sum())
                leak = int((~inside & valid).sum())
                miss_ratio = miss / max(n_in, 1)
                # 源数据本身允许极少像元缺测（B 的光学合成就有 8 个），但不允许成片漏覆盖
                if miss_ratio > 1e-3:
                    fail("[覆盖率] 研究区内 %d 像元（%.3f%%）为 NoData，超过 0.1%% 上限"
                         % (miss, 100.0 * miss_ratio))
                elif miss:
                    warn("[覆盖率] 研究区内 %d 像元（%.4f%%）为 NoData，应为源数据缺测"
                         % (miss, 100.0 * miss_ratio))
                else:
                    ok("[覆盖率] 研究区 %d 像元（%.3f km2）全部有效"
                       % (n_in, n_in * 100.0 / 1e6))
                if leak:
                    warn("[覆盖率] 研究区外仍有 %d 像元有效（应置 NoData）" % leak)
                else:
                    ok("[覆盖率] 研究区外全部为 NoData")
        elif cover < 0.99:
            warn("[flood_probability] 有效像元仅占 %.1f%%，检查是否漏覆盖研究区" % (cover * 100))
    else:
        fail("[flood_probability] 全部是 NoData")

    # mask
    arr_m, nod_m, dt_m = arrays["flood_mask"]
    if dt_m != "uint8":
        fail("[flood_mask] dtype 应为 uint8，实际 %s" % dt_m)
    if nod_m is not None and int(nod_m) != MASK_NODATA:
        fail("[flood_mask] nodata 应为 %d，实际 %r" % (MASK_NODATA, nod_m))
    uniq = set(np.unique(arr_m).tolist())
    illegal = uniq - {0, 1, MASK_NODATA}
    if illegal:
        fail("[flood_mask] 出现非法值 %s（只允许 0/1/255）" % sorted(illegal))
    else:
        ok("[flood_mask] 取值集合 %s" % sorted(uniq))

    # uncertainty
    arr_u, nod_u, dt_u = arrays["uncertainty"]
    if dt_u != "float32":
        fail("[uncertainty] dtype 应为 float32，实际 %s" % dt_u)
    if nod_u is None or abs(float(nod_u) - FLOAT_NODATA) > 1e-6:
        fail("[uncertainty] nodata 应为 %s，实际 %r" % (FLOAT_NODATA, nod_u))
    vu = arr_u != (FLOAT_NODATA if nod_u is None else nod_u)
    if vu.any():
        umin, umax = float(arr_u[vu].min()), float(arr_u[vu].max())
        if umin < -1e-9 or umax > UNC_MAX + 1e-6:
            fail("[uncertainty] 值域 [%.4f, %.4f] 超出 [0, %.2f]" % (umin, umax, UNC_MAX))
        else:
            ok("[uncertainty] 值域 [%.4f, %.4f]" % (umin, umax))
        if umax <= 0:
            warn("[uncertainty] 全为 0，集成方差未真正生效")

    # mask <-> prob 一致性
    if threshold is not None:
        both = valid & (arr_m != MASK_NODATA)
        m1 = both & (arr_m == 1)
        if m1.sum() > 0:
            bad = int((arr[m1] < threshold - 1e-6).sum())
            ratio = bad / float(m1.sum())
            if ratio > 0.01:
                fail("[一致性] mask=1 但 prob<threshold 的比例 %.2f%%，超过 1%%" % (ratio * 100))
            else:
                ok("[一致性] mask=1 处 prob>=threshold 的吻合率 %.2f%%" % ((1 - ratio) * 100))
        else:
            warn("[一致性] mask 中没有 1 像元，无法做一致性检查")
        m0 = both & (arr_m == 0)
        if m0.sum() > 0:
            bad0 = int((arr[m0] >= threshold + 1e-6).sum())
            r0 = bad0 / float(m0.sum())
            if r0 > 0.05:
                warn("[一致性] mask=0 但 prob>=threshold 的比例 %.2f%%" % (r0 * 100))

    return res


def _write_raster(path, arr, dtype, crs, transform, nodata):
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
        count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(arr.astype(dtype), 1)


def selftest():
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    crs = "EPSG:32649"
    tr = from_origin(500000.0, 2500000.0, 10.0, 10.0)
    np.random.seed(0)

    ref = os.path.join(tmp, "ref.tif")
    _write_raster(ref, np.zeros((64, 64), np.uint8), "uint8", crs, tr, 255)

    prob = np.random.rand(64, 64).astype(np.float32)
    prob[:2, :] = FLOAT_NODATA
    mask = np.where(prob >= 0.5, 1, 0).astype(np.uint8)
    mask[:2, :] = MASK_NODATA
    unc = (np.random.rand(64, 64) * 0.2).astype(np.float32)
    unc[:2, :] = FLOAT_NODATA

    _write_raster(os.path.join(tmp, "x_flood_probability_candidate_v01.tif"), prob, "float32", crs, tr, FLOAT_NODATA)
    _write_raster(os.path.join(tmp, "x_flood_mask_candidate_v01.tif"), mask, "uint8", crs, tr, MASK_NODATA)
    _write_raster(os.path.join(tmp, "x_uncertainty_candidate_v01.tif"), unc, "float32", crs, tr, FLOAT_NODATA)

    meta = {
        "model_version": "selftest", "status": "candidate", "region": "selftest",
        "threshold": 0.5, "threshold_selected_on": "validation",
        "label_semantics": "post_event_water_including_permanent",
        "crs": "EPSG:32649", "resolution_m": 10, "reference_grid": "ref.tif",
        "ensemble_members": 3, "training_seed": 2026,
        "evaluation": {"iou": 0.0, "dice": 0.0, "f1": 0.0, "precision": 0.0, "recall": 0.0,
                       "area_abs_error_km2": 0.0, "area_rel_error": 0.0},
        "calibration": {"method": "none", "brier": 0.0, "ece": 0.0},
        "split_rule": "block_level_6_2_2", "git_commit": "", "created_at": "2026-11-27T00:00:00",
    }
    with open(os.path.join(tmp, "x_flood_model_metadata_candidate_v01.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("=" * 68)
    print("[自测 1] 合规数据，期望 PASS")
    print("=" * 68)
    r1 = check_dir(tmp, ref)
    for lv, m in r1:
        print("  %-4s %s" % (lv, m))
    pass1 = not any(lv == "FAIL" for lv, _ in r1)

    # 制造违规：概率越界 + mask 非法值
    bad = prob.copy()
    bad[5, 5] = 1.5
    _write_raster(os.path.join(tmp, "x_flood_probability_final_v02.tif"), bad, "float32", crs, tr, FLOAT_NODATA)
    print()
    print("=" * 68)
    print("[自测 2] 概率越界，期望 FAIL")
    print("=" * 68)
    r2 = check_dir(tmp, ref)
    for lv, m in r2:
        print("  %-4s %s" % (lv, m))
    pass2 = any(lv == "FAIL" and "值域越界" in m for lv, m in r2)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("自测结果: 用例1 %s / 用例2 %s" % ("通过" if pass1 else "失败", "通过" if pass2 else "失败"))
    return 0 if (pass1 and pass2) else 1


def main():
    ap = argparse.ArgumentParser(description="A→B 栅格接口校验器")
    ap.add_argument("--dir", help="A 输出的目录")
    ap.add_argument("--ref", help="参考栅格 reference grid")
    ap.add_argument("--study-mask", default=None,
                    help="研究区掩膜（!=0 为研究区内），给定后按掩膜判覆盖率")
    ap.add_argument("--selftest", action="store_true", help="自检（不需要真实数据）")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.dir or not a.ref:
        ap.print_help()
        return 2

    print("目录: %s" % a.dir)
    print("参考: %s" % a.ref)
    print("-" * 68)
    res = check_dir(a.dir, a.ref, a.study_mask)
    for lv, m in res:
        print("  %-4s %s" % (lv, m))
    nf = sum(1 for lv, _ in res if lv == "FAIL")
    nw = sum(1 for lv, _ in res if lv == "WARN")
    print("-" * 68)
    if nf:
        print("结论: 不通过（%d 项致命, %d 项警告）—— 禁止交付 B" % (nf, nw))
        return 1
    print("结论: 通过（0 项致命, %d 项警告）—— 可以交付 B" % nw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
