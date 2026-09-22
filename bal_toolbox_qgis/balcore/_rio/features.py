"""Feature rasterisation mirroring the subset of :mod:`rasterio.features` used.

Implements, against GDAL/OGR:

* :func:`bounds` -- the bounding box of a GeoJSON geometry mapping;
* :func:`geometry_mask` -- a boolean mask of cells inside/outside geometries;
* :func:`rasterize` -- burn ``(geometry, value)`` pairs into an array, with
  ``all_touched`` and ``MergeAlg`` (replace/add) semantics.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

import numpy as np
from osgeo import gdal, ogr

from ._util import np_to_gdal_dtype
from .affine import Affine
from .enums import MergeAlg
from .errors import RasterioError

__all__ = ["bounds", "geometry_mask", "rasterize"]


def _walk_coords(coords: Any, xs: list[float], ys: list[float]) -> None:
    """Collect x/y values from a nested GeoJSON coordinate structure."""
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        xs.append(float(coords[0]))
        ys.append(float(coords[1]))
        return
    if isinstance(coords, (list, tuple)):
        for part in coords:
            _walk_coords(part, xs, ys)


def bounds(geometry: dict[str, Any], **kwargs: Any) -> tuple[float, float, float, float]:
    """Return ``(minx, miny, maxx, maxy)`` for a GeoJSON geometry mapping.

    Mirrors :func:`rasterio.features.bounds` for a single geometry
    (including ``GeometryCollection``).

    Raises:
        ValueError: If the geometry contains no coordinates.
    """
    xs: list[float] = []
    ys: list[float] = []
    if geometry.get("type") == "GeometryCollection":
        for sub in geometry.get("geometries", []):
            _walk_coords(sub.get("coordinates", []), xs, ys)
    else:
        _walk_coords(geometry.get("coordinates", []), xs, ys)
    if not xs:
        raise ValueError("Geometry has no coordinates")
    return min(xs), min(ys), max(xs), max(ys)


def _build_layer(
    shapes: Sequence[tuple[dict[str, Any], float]],
) -> tuple["ogr.DataSource", "ogr.Layer"]:
    """Create an in-memory OGR layer with a ``bval`` burn-value field."""
    driver = ogr.GetDriverByName("Memory")
    datasource = driver.CreateDataSource("burn")
    layer = datasource.CreateLayer("shapes", srs=None, geom_type=ogr.wkbUnknown)
    layer.CreateField(ogr.FieldDefn("bval", ogr.OFTReal))
    defn = layer.GetLayerDefn()
    for geom, value in shapes:
        feature = ogr.Feature(defn)
        feature.SetField("bval", float(value))
        ogr_geom = ogr.CreateGeometryFromJson(json.dumps(geom))
        if ogr_geom is None:
            raise RasterioError(f"Could not parse geometry: {geom!r}")
        feature.SetGeometry(ogr_geom)
        layer.CreateFeature(feature)
        feature = None
    return datasource, layer


def rasterize(
    shapes: Iterable[tuple[dict[str, Any], float]],
    out_shape: tuple[int, int],
    fill: float = 0,
    transform: Affine | None = None,
    all_touched: bool = False,
    merge_alg: MergeAlg = MergeAlg.replace,
    default_value: float = 1,
    dtype: Any = None,
    **kwargs: Any,
) -> np.ndarray[Any, np.dtype[Any]]:
    """Burn ``(geometry, value)`` pairs into a new array.

    Mirrors :func:`rasterio.features.rasterize` for the forms the core
    uses: ``all_touched`` sampling, ``MergeAlg.add`` (to count overlapping
    fire polygons) and ``MergeAlg.replace`` (the default mask burn).

    Args:
        shapes: Iterable of ``(geojson_geometry, burn_value)`` pairs.
        out_shape: ``(rows, cols)`` of the output array.
        fill: Background value for untouched cells.
        transform: Affine transform mapping pixel to world coordinates.
        all_touched: Burn every cell a geometry touches, not just those
            whose centre is covered.
        merge_alg: ``MergeAlg.replace`` (overwrite) or ``MergeAlg.add``
            (accumulate).
        default_value: Accepted for API parity; burn values come from the
            pairs.
        dtype: Output numpy dtype (defaults to float64).

    Returns:
        The rasterised array of shape ``out_shape``.
    """
    if transform is None:
        raise RasterioError("rasterize requires a transform")

    pairs = list(shapes)
    rows, cols = int(out_shape[0]), int(out_shape[1])
    np_dtype = np.dtype(dtype) if dtype is not None else np.dtype(np.float64)

    mem = gdal.GetDriverByName("MEM").Create(
        "", cols, rows, 1, np_to_gdal_dtype(np_dtype)
    )
    mem.SetGeoTransform(transform.to_gdal())
    band = mem.GetRasterBand(1)
    band.Fill(float(fill))

    if pairs:
        datasource, layer = _build_layer(pairs)
        options = ["ATTRIBUTE=bval"]
        if all_touched:
            options.append("ALL_TOUCHED=TRUE")
        merge = merge_alg.value if isinstance(merge_alg, MergeAlg) else str(merge_alg)
        if str(merge).upper() == "ADD":
            options.append("MERGE_ALG=ADD")
        err = gdal.RasterizeLayer(mem, [1], layer, options=options)
        # SWIG returns 0 (CE_None) on success; some builds return None.
        if err not in (0, None):
            raise RasterioError("gdal.RasterizeLayer failed")
        datasource = None

    result = band.ReadAsArray()
    mem = None
    return np.asarray(result).astype(np_dtype)


def geometry_mask(
    geometries: Iterable[dict[str, Any]],
    out_shape: tuple[int, int],
    transform: Affine,
    all_touched: bool = False,
    invert: bool = False,
    **kwargs: Any,
) -> np.ndarray[Any, np.dtype[Any]]:
    """Return a boolean mask for cells relative to ``geometries``.

    Mirrors :func:`rasterio.features.geometry_mask`. With the default
    ``invert=False`` the result is ``True`` *outside* the geometries (the
    numpy "masked" convention); with ``invert=True`` it is ``True``
    *inside* the geometries.

    Args:
        geometries: Iterable of GeoJSON geometry mappings.
        out_shape: ``(rows, cols)`` of the output mask.
        transform: Affine transform mapping pixel to world coordinates.
        all_touched: Include every cell a geometry touches.
        invert: Return ``True`` inside geometries instead of outside.

    Returns:
        A boolean array of shape ``out_shape``.
    """
    shapes = [(geom, 1) for geom in geometries]
    burned = rasterize(
        shapes,
        out_shape=out_shape,
        fill=0,
        transform=transform,
        all_touched=all_touched,
        merge_alg=MergeAlg.replace,
        dtype=np.uint8,
    ).astype(bool)
    if invert:
        return burned
    return ~burned
