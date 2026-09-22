"""Warping helpers mirroring the subset of :mod:`rasterio.warp` used.

Implements, against GDAL:

* :func:`transform` -- reproject arrays of x/y coordinates;
* :func:`transform_bounds` -- reproject a bounding box (densified edges);
* :func:`transform_geom` -- reproject a GeoJSON geometry mapping;
* :func:`reproject` -- warp a source array onto a destination array/grid;
* :func:`calculate_default_transform` -- suggest an output transform and
  size for a reprojection (optionally at a fixed resolution).

All coordinate operations run through ``osr.SpatialReference`` objects in
traditional GIS (x/y) axis order, so results match rasterio's always-xy
convention.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Sequence

import numpy as np
from osgeo import gdal, osr

from ._util import mem_dataset_from_array, wkt_of
from .affine import Affine
from .crs import CRS, CRSLike
from .enums import Resampling
from .errors import RasterioError

__all__ = [
    "transform",
    "transform_bounds",
    "transform_geom",
    "reproject",
    "calculate_default_transform",
]


@lru_cache(maxsize=64)
def _cached_transform(src_wkt: str, dst_wkt: str) -> osr.CoordinateTransformation:
    """Return a cached ``CoordinateTransformation`` between two WKT CRSs."""
    src = osr.SpatialReference()
    src.ImportFromWkt(src_wkt)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromWkt(dst_wkt)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(src, dst)


def _coordinate_transformation(
    src_crs: CRSLike, dst_crs: CRSLike
) -> osr.CoordinateTransformation:
    """Build (or reuse) a coordinate transformation between two CRSs."""
    src_wkt = wkt_of(src_crs)
    dst_wkt = wkt_of(dst_crs)
    if not src_wkt or not dst_wkt:
        raise RasterioError("Both source and destination CRS are required")
    return _cached_transform(src_wkt, dst_wkt)


def _resample_alg(resampling: Any) -> int:
    """Map a :class:`Resampling` (or raw GDAL code) to a GDAL resample alg."""
    if isinstance(resampling, Resampling):
        return int(resampling.value)
    return int(resampling)


def transform(
    src_crs: CRSLike,
    dst_crs: CRSLike,
    xs: Sequence[float],
    ys: Sequence[float],
    zs: Sequence[float] | None = None,
    **kwargs: Any,
) -> tuple[list[float], list[float]]:
    """Reproject sequences of coordinates from ``src_crs`` to ``dst_crs``.

    Mirrors :func:`rasterio.warp.transform`, returning ``(xs, ys)`` lists.
    """
    ct = _coordinate_transformation(src_crs, dst_crs)
    out_xs: list[float] = []
    out_ys: list[float] = []
    for x, y in zip(xs, ys):
        px, py, _ = ct.TransformPoint(float(x), float(y))
        out_xs.append(px)
        out_ys.append(py)
    return out_xs, out_ys


def transform_bounds(
    src_crs: CRSLike,
    dst_crs: CRSLike,
    left: float,
    bottom: float,
    right: float,
    top: float,
    densify_pts: int = 21,
    **kwargs: Any,
) -> tuple[float, float, float, float]:
    """Reproject a bounding box, densifying its edges for accuracy.

    Mirrors :func:`rasterio.warp.transform_bounds`: samples points along
    the four edges of the box, transforms them, and returns the enclosing
    ``(left, bottom, right, top)`` in the destination CRS.
    """
    ct = _coordinate_transformation(src_crs, dst_crs)
    steps = max(int(densify_pts), 0) + 1

    xs: list[float] = []
    ys: list[float] = []

    def _edge(x0: float, y0: float, x1: float, y1: float) -> None:
        for k in range(steps + 1):
            t = k / steps
            xs.append(x0 + (x1 - x0) * t)
            ys.append(y0 + (y1 - y0) * t)

    _edge(left, bottom, left, top)  # west edge
    _edge(right, bottom, right, top)  # east edge
    _edge(left, bottom, right, bottom)  # south edge
    _edge(left, top, right, top)  # north edge

    txs: list[float] = []
    tys: list[float] = []
    for x, y in zip(xs, ys):
        px, py, _ = ct.TransformPoint(float(x), float(y))
        txs.append(px)
        tys.append(py)
    return min(txs), min(tys), max(txs), max(tys)


def _transform_coords(coords: Any, ct: osr.CoordinateTransformation) -> Any:
    """Recursively transform a (possibly nested) GeoJSON coordinate list."""
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        px, py, _ = ct.TransformPoint(float(coords[0]), float(coords[1]))
        rest = list(coords[2:])
        return [px, py, *rest]
    return [_transform_coords(part, ct) for part in coords]


def transform_geom(
    src_crs: CRSLike,
    dst_crs: CRSLike,
    geom: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Reproject a single GeoJSON geometry mapping.

    Mirrors :func:`rasterio.warp.transform_geom` for a single geometry
    (the only form the core uses), including ``GeometryCollection``.
    """
    ct = _coordinate_transformation(src_crs, dst_crs)
    geom_type = geom.get("type")
    if geom_type == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [
                transform_geom(src_crs, dst_crs, sub) for sub in geom.get("geometries", [])
            ],
        }
    return {
        "type": geom_type,
        "coordinates": _transform_coords(geom["coordinates"], ct),
    }


def reproject(
    source: np.ndarray[Any, np.dtype[Any]],
    destination: np.ndarray[Any, np.dtype[Any]],
    src_transform: Affine | None = None,
    src_crs: CRSLike = None,
    dst_transform: Affine | None = None,
    dst_crs: CRSLike = None,
    src_nodata: float | None = None,
    dst_nodata: float | None = None,
    resampling: Any = Resampling.nearest,
    **kwargs: Any,
) -> tuple[np.ndarray[Any, np.dtype[Any]], Affine]:
    """Warp ``source`` onto the grid defined by ``destination``.

    Mirrors :func:`rasterio.warp.reproject` for the single-band, array-to-
    array form the core uses. The result is written *in place* into
    ``destination`` (which also fixes the output grid via its shape and
    ``dst_transform``), and ``(destination, dst_transform)`` is returned.

    Cells not covered by the source are filled with ``dst_nodata``.
    """
    if src_transform is None or dst_transform is None:
        raise RasterioError("reproject requires src_transform and dst_transform")

    src_ds = mem_dataset_from_array(source, src_transform, src_crs, src_nodata)

    rows, cols = destination.shape
    left, top = dst_transform * (0.0, 0.0)
    right, bottom = dst_transform * (float(cols), float(rows))
    minx, maxx = min(left, right), max(left, right)
    miny, maxy = min(top, bottom), max(top, bottom)

    warp_options: dict[str, Any] = {
        "format": "MEM",
        "width": int(cols),
        "height": int(rows),
        "outputBounds": (minx, miny, maxx, maxy),
        "resampleAlg": _resample_alg(resampling),
    }
    dst_wkt = wkt_of(dst_crs)
    if dst_wkt:
        warp_options["dstSRS"] = dst_wkt
    src_wkt = wkt_of(src_crs)
    if src_wkt:
        warp_options["srcSRS"] = src_wkt
    if src_nodata is not None:
        warp_options["srcNodata"] = src_nodata
    if dst_nodata is not None:
        warp_options["dstNodata"] = dst_nodata

    warped = gdal.Warp("", src_ds, **warp_options)
    if warped is None:
        raise RasterioError("gdal.Warp failed during reproject")

    result = warped.GetRasterBand(1).ReadAsArray()
    destination[:] = result.astype(destination.dtype)
    return destination, dst_transform


def calculate_default_transform(
    src_crs: CRSLike,
    dst_crs: CRSLike,
    width: int,
    height: int,
    left: float | None = None,
    bottom: float | None = None,
    right: float | None = None,
    top: float | None = None,
    gcps: Any = None,
    rpcs: Any = None,
    resolution: float | tuple[float, float] | None = None,
    dst_width: int | None = None,
    dst_height: int | None = None,
    **kwargs: Any,
) -> tuple[Affine, int, int]:
    """Suggest an output transform and size for reprojecting a raster.

    Mirrors :func:`rasterio.warp.calculate_default_transform`, wrapping
    GDAL's suggested-warp-output logic (``AutoCreateWarpedVRT``). When
    ``resolution`` is given, the output is snapped to that pixel size (as
    the DEM reprojection relies on to force a 30 m grid).

    Returns:
        ``(transform, width, height)`` for the destination grid.
    """
    if left is None or bottom is None or right is None or top is None:
        raise RasterioError("calculate_default_transform requires bounds")

    src_wkt = wkt_of(src_crs)
    dst_wkt = wkt_of(dst_crs)

    px = (right - left) / width
    py = -(top - bottom) / height
    src_transform = Affine(px, 0.0, left, 0.0, py, top)

    src_ds = gdal.GetDriverByName("MEM").Create("", int(width), int(height), 1, gdal.GDT_Byte)
    src_ds.SetGeoTransform(src_transform.to_gdal())
    if src_wkt:
        src_ds.SetProjection(src_wkt)

    vrt = gdal.AutoCreateWarpedVRT(src_ds, src_wkt or None, dst_wkt or None)
    if vrt is None:
        raise RasterioError("gdal.AutoCreateWarpedVRT failed")

    gt = vrt.GetGeoTransform()
    out_w = vrt.RasterXSize
    out_h = vrt.RasterYSize

    ext_left = gt[0]
    ext_top = gt[3]
    ext_right = gt[0] + out_w * gt[1]
    ext_bottom = gt[3] + out_h * gt[5]

    if resolution is not None:
        if isinstance(resolution, (tuple, list)):
            res_x, res_y = float(resolution[0]), float(resolution[1])
        else:
            res_x = res_y = float(resolution)
        out_w = max(1, int(round((ext_right - ext_left) / res_x)))
        out_h = max(1, int(round((ext_top - ext_bottom) / res_y)))
        transform_out = Affine(res_x, 0.0, ext_left, 0.0, -res_y, ext_top)
    else:
        transform_out = Affine.from_gdal(*gt)

    if dst_width is not None:
        out_w = int(dst_width)
    if dst_height is not None:
        out_h = int(dst_height)

    return transform_out, out_w, out_h
