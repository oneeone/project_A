"""成员A 公共模块：路径、附件读取、地理标签、椭球像素面积。

约定（见 configs/spatial.yaml）：
- 附件原始坐标系 EPSG:4326，像元 0.00008333 度
- 标签 1 = 新增淹没，0 = 非新增，255 = 忽略
- 面积一律用椭球像素面积换算，禁止用 10m x 10m = 100 m2
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]

WGS84_A = 6378137.0
WGS84_E2 = 0.00669437999014

# 标签取值
LABEL_IGNORE = 255


def load_paths() -> dict:
    """读取本机路径配置。"""
    with open(ROOT / "configs" / "paths.local.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _tiepoint_center(img: Image.Image) -> tuple[float, float]:
    """从 GeoTIFF tag 读出 patch 中心经纬度。"""
    tags = img.tag_v2
    sx = tags[33550][0]
    sy = tags[33550][1]
    tie = tags[33922]  # I, J, K, X, Y, Z
    lon0, lat0 = float(tie[3]), float(tie[4])
    w, h = img.size
    return lon0 + w * 0.5 * sx, lat0 - h * 0.5 * sy


def _pixel_deg(img: Image.Image) -> tuple[float, float]:
    tags = img.tag_v2
    return float(tags[33550][0]), float(tags[33550][1])


def ellipsoid_pixel_area_m2(lat_deg: float, dlon_deg: float, dlat_deg: float) -> float:
    """WGS84 椭球上一个小经纬度矩形的面积（平方米）。"""
    lat = math.radians(lat_deg)
    w = math.sqrt(1.0 - WGS84_E2 * math.sin(lat) ** 2)
    m_per_deg_lat = math.pi / 180.0 * WGS84_A * (1.0 - WGS84_E2) / (w ** 3)
    m_per_deg_lon = math.pi / 180.0 * WGS84_A * math.cos(lat) / w
    return abs(dlon_deg) * m_per_deg_lon * abs(dlat_deg) * m_per_deg_lat


def list_patches(paths: dict) -> list[dict]:
    """扫描附件目录，返回 1000 条 patch 记录（按编号排序）。"""
    opt_dir = Path(paths["provided_optical_dir"])
    sar_dir = Path(paths["provided_sar_dir"])
    lab_dir = Path(paths["provided_label_dir"])

    out = []
    for i in range(1001, 2001):
        sar = sar_dir / f"after_gf3_hh_{i}.TIF"
        lab = lab_dir / f"label_{i}.tif"
        opt = opt_dir / f"before_s2_{i}.TIF"
        missing = [str(p) for p in (sar, lab, opt) if not p.exists()]
        if missing:
            raise FileNotFoundError(f"patch {i} 缺文件: {missing}")

        with Image.open(sar) as im:
            lon, lat = _tiepoint_center(im)
            dlon, dlat = _pixel_deg(im)

        out.append({
            "patch_id": i,
            "sar": str(sar),
            "label": str(lab),
            "optical": str(opt),
            "lon": round(lon, 6),
            "lat": round(lat, 6),
            "pixel_dlon": dlon,
            "pixel_dlat": dlat,
            "pixel_area_m2": ellipsoid_pixel_area_m2(lat, dlon, dlat),
        })
    return out


def read_sar(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im, dtype=np.uint8)


def read_label(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im, dtype=np.uint8)


def read_optical(path: str) -> np.ndarray:
    """返回 (4, H, W) uint8。"""
    with Image.open(path) as im:
        arr = np.asarray(im, dtype=np.uint8)
    if arr.ndim == 3 and arr.shape[-1] == 4:
        return np.transpose(arr, (2, 0, 1))
    if arr.ndim == 3 and arr.shape[0] == 4:
        return arr
    raise ValueError(f"光学影像通道异常: {path} shape={arr.shape}")


def region_of(lon: float, split_lon: float = 115.0) -> str:
    """附件跨两个主要区域，以 115E 为界。"""
    return "west" if lon < split_lon else "east"
