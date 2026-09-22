"""Full GDAL-vs-rasterio equivalence tests for the ``_rio`` shim.

The shim reproduces the rasterio API against ``osgeo`` (GDAL). These tests
drive both the shim and genuine rasterio through the same operations and
assert matching results, covering the GDAL-backed modules (``crs``,
``warp``, ``features``, ``io``).

They require ``osgeo``, which is not available in the plain sandbox but is
present in every QGIS install -- so the module is skipped where GDAL's
Python bindings are absent and runs in full inside QGIS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("osgeo", reason="GDAL Python bindings (osgeo) required")

import affine as _affine
import rasterio
import rasterio.features as rio_features
import rasterio.warp as rio_warp
from bal_toolbox_qgis.balcore import _rio
from bal_toolbox_qgis.balcore._rio import crs as shim_crs
from bal_toolbox_qgis.balcore._rio import features as shim_features
from bal_toolbox_qgis.balcore._rio import warp as shim_warp
from bal_toolbox_qgis.balcore._rio.affine import Affine
from bal_toolbox_qgis.balcore._rio.enums import MergeAlg, Resampling
from rasterio.crs import CRS as RioCRS
from rasterio.enums import MergeAlg as RioMergeAlg
from rasterio.enums import Resampling as RioResampling

MGA54 = "EPSG:7854"
GDA94 = "EPSG:4283"
WGS84 = "EPSG:4326"


# --------------------------------------------------------------------------
# CRS
# --------------------------------------------------------------------------
def test_crs_epsg_roundtrip_and_equality() -> None:
    a = shim_crs.CRS.from_user_input(GDA94)
    b = shim_crs.CRS.from_epsg(4283)
    assert a == b
    assert a.to_epsg() == 4283
    assert a.to_string() == "EPSG:4283"
    assert a != shim_crs.CRS.from_epsg(4326)


def test_crs_is_geographic_matches_rasterio() -> None:
    for code in (WGS84, GDA94, MGA54, "EPSG:3577"):
        ours = shim_crs.CRS.from_user_input(code).is_geographic
        ref = RioCRS.from_user_input(code).is_geographic
        assert ours == ref


def test_crs_invalid_raises() -> None:
    with pytest.raises(_rio.errors.CRSError):
        shim_crs.CRS.from_user_input("not-a-crs-!!")


# --------------------------------------------------------------------------
# warp.transform / transform_bounds / transform_geom
# --------------------------------------------------------------------------
def test_transform_points_matches_rasterio() -> None:
    xs = [138.6, 138.7, 138.8]
    ys = [-34.9, -34.8, -34.7]
    ox, oy = shim_warp.transform(GDA94, MGA54, xs, ys)
    rx, ry = rio_warp.transform(
        RioCRS.from_string(GDA94), RioCRS.from_string(MGA54), xs, ys
    )
    assert ox == pytest.approx(rx, abs=1e-3)
    assert oy == pytest.approx(ry, abs=1e-3)


def test_transform_bounds_matches_rasterio() -> None:
    bounds = (138.55, -35.05, 138.95, -34.75)
    ours = shim_warp.transform_bounds(GDA94, MGA54, *bounds)
    ref = rio_warp.transform_bounds(
        RioCRS.from_string(GDA94), RioCRS.from_string(MGA54), *bounds
    )
    assert ours == pytest.approx(ref, abs=1.0)


def test_transform_geom_matches_rasterio() -> None:
    geom = {
        "type": "Polygon",
        "coordinates": [
            [
                [138.60, -34.90],
                [138.62, -34.90],
                [138.62, -34.88],
                [138.60, -34.88],
                [138.60, -34.90],
            ]
        ],
    }
    ours = shim_warp.transform_geom(GDA94, MGA54, geom)
    ref = rio_warp.transform_geom(
        RioCRS.from_string(GDA94), RioCRS.from_string(MGA54), geom
    )
    ours_ring = np.array(ours["coordinates"][0])
    ref_ring = np.array(ref["coordinates"][0])
    assert ours_ring == pytest.approx(ref_ring, abs=1e-3)


# --------------------------------------------------------------------------
# warp.calculate_default_transform
# --------------------------------------------------------------------------
def test_calculate_default_transform_matches_rasterio() -> None:
    src_crs, dst_crs = WGS84, MGA54
    width, height = 100, 80
    left, bottom, right, top = 138.5, -35.1, 139.0, -34.7
    ours_t, ours_w, ours_h = shim_warp.calculate_default_transform(
        src_crs, dst_crs, width, height, left, bottom, right, top
    )
    ref_t, ref_w, ref_h = rio_warp.calculate_default_transform(
        RioCRS.from_string(src_crs),
        RioCRS.from_string(dst_crs),
        width,
        height,
        left=left,
        bottom=bottom,
        right=right,
        top=top,
    )
    # Allow one pixel of slack in the suggested size.
    assert abs(ours_w - ref_w) <= 1
    assert abs(ours_h - ref_h) <= 1
    assert tuple(ours_t)[:6] == pytest.approx(tuple(ref_t)[:6], rel=1e-3, abs=1.0)


def test_calculate_default_transform_fixed_resolution() -> None:
    # The DEM reprojection forces a 30 m grid via ``resolution=30.0``.
    ours_t, ours_w, ours_h = shim_warp.calculate_default_transform(
        WGS84, MGA54, 200, 160, 138.5, -35.1, 139.0, -34.7, resolution=30.0
    )
    assert ours_t.a == pytest.approx(30.0)
    assert ours_t.e == pytest.approx(-30.0)
    assert ours_w > 0 and ours_h > 0


# --------------------------------------------------------------------------
# features.bounds / geometry_mask / rasterize
# --------------------------------------------------------------------------
def _square(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def test_features_bounds_matches_rasterio() -> None:
    geom = _square(10, 20, 40, 55)
    assert shim_features.bounds(geom) == pytest.approx(rio_features.bounds(geom))


def test_geometry_mask_matches_rasterio() -> None:
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 100.0)
    rio_t = _affine.Affine(1.0, 0.0, 0.0, 0.0, -1.0, 100.0)
    geoms = [_square(10.5, 80.5, 30.5, 95.5)]
    for invert in (False, True):
        for all_touched in (False, True):
            ours = shim_features.geometry_mask(
                geoms, (100, 100), transform, invert=invert, all_touched=all_touched
            )
            ref = rio_features.geometry_mask(
                geoms,
                out_shape=(100, 100),
                transform=rio_t,
                invert=invert,
                all_touched=all_touched,
            )
            assert np.array_equal(ours, ref), (invert, all_touched)


def test_rasterize_replace_matches_rasterio() -> None:
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 100.0)
    rio_t = _affine.Affine(1.0, 0.0, 0.0, 0.0, -1.0, 100.0)
    shapes = [(_square(10, 60, 40, 90), 1)]
    ours = shim_features.rasterize(
        shapes,
        (100, 100),
        fill=0,
        transform=transform,
        all_touched=True,
        dtype=np.uint8,
    )
    ref = rio_features.rasterize(
        shapes,
        out_shape=(100, 100),
        fill=0,
        transform=rio_t,
        all_touched=True,
        dtype="uint8",
    )
    assert np.array_equal(ours, ref)


def test_rasterize_merge_add_counts_overlaps() -> None:
    # Two overlapping polygons with MergeAlg.add must accumulate to 2 in
    # the overlap -- the behaviour the fire-history count layer relies on.
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 100.0)
    rio_t = _affine.Affine(1.0, 0.0, 0.0, 0.0, -1.0, 100.0)
    shapes = [(_square(10, 60, 50, 90), 1), (_square(30, 60, 70, 90), 1)]
    ours = shim_features.rasterize(
        shapes,
        (100, 100),
        fill=0,
        transform=transform,
        all_touched=True,
        merge_alg=MergeAlg.add,
        dtype=np.int32,
    )
    ref = rio_features.rasterize(
        shapes,
        out_shape=(100, 100),
        fill=0,
        transform=rio_t,
        all_touched=True,
        merge_alg=RioMergeAlg.add,
        dtype="int32",
    )
    assert np.array_equal(ours, ref)
    assert ours.max() == 2


# --------------------------------------------------------------------------
# warp.reproject
# --------------------------------------------------------------------------
def test_reproject_matches_rasterio() -> None:
    src = np.arange(2500, dtype=np.float32).reshape(50, 50)
    src_transform = Affine(0.01, 0.0, 138.5, 0.0, -0.01, -34.5)
    rio_src_t = _affine.Affine(0.01, 0.0, 138.5, 0.0, -0.01, -34.5)
    dst_transform, dst_w, dst_h = shim_warp.calculate_default_transform(
        WGS84, MGA54, 50, 50, 138.5, -35.0, 139.0, -34.5
    )
    ours = np.zeros((dst_h, dst_w), dtype=np.float32)
    shim_warp.reproject(
        source=src,
        destination=ours,
        src_transform=src_transform,
        src_crs=WGS84,
        dst_transform=dst_transform,
        dst_crs=MGA54,
        src_nodata=-9999.0,
        dst_nodata=-9999.0,
        resampling=Resampling.bilinear,
    )
    ref = np.zeros((dst_h, dst_w), dtype=np.float32)
    rio_warp.reproject(
        source=src,
        destination=ref,
        src_transform=rio_src_t,
        src_crs=RioCRS.from_string(WGS84),
        dst_transform=_affine.Affine(*tuple(dst_transform)[:6]),
        dst_crs=RioCRS.from_string(MGA54),
        src_nodata=-9999.0,
        dst_nodata=-9999.0,
        resampling=RioResampling.bilinear,
    )
    valid = (ref != -9999.0) & (ours != -9999.0)
    assert valid.sum() > 0
    assert ours[valid] == pytest.approx(ref[valid], rel=1e-2, abs=1.0)


# --------------------------------------------------------------------------
# io read/write roundtrip
# --------------------------------------------------------------------------
def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    data = (np.arange(400, dtype=np.float32).reshape(20, 20) / 7.0).astype(np.float32)
    transform = Affine(30.0, 0.0, 500000.0, 0.0, -30.0, 6100000.0)
    path = tmp_path / "roundtrip.tif"
    profile = {
        "driver": "GTiff",
        "height": 20,
        "width": 20,
        "count": 1,
        "dtype": np.dtype(np.float32),
        "nodata": -9999.0,
        "transform": transform,
        "crs": shim_crs.CRS.from_user_input(MGA54),
        "compress": "deflate",
    }
    with _rio.open(str(path), "w", **profile) as dst:
        dst.write(data, 1)

    with _rio.open(str(path)) as src:
        assert src.width == 20
        assert src.height == 20
        assert src.nodata == pytest.approx(-9999.0)
        assert tuple(src.transform)[:6] == pytest.approx(tuple(transform)[:6])
        assert src.crs is not None
        assert src.crs.to_epsg() == 7854
        read_back = src.read(1)
    assert read_back == pytest.approx(data, abs=1e-4)

    # Cross-check the file is readable by genuine rasterio too.
    with rasterio.open(str(path)) as rio_src:
        assert rio_src.width == 20
        assert rio_src.crs.to_epsg() == 7854
        assert rio_src.read(1) == pytest.approx(data, abs=1e-4)
