"""Shared helpers for the GDAL-backed rasterio shim (internal)."""

from __future__ import annotations

from typing import Any

import numpy as np
from osgeo import gdal, gdal_array

from .crs import CRS, CRSLike


def np_to_gdal_dtype(dtype: Any) -> int:
    """Map a numpy dtype to the closest GDAL data type code.

    Falls back to ``Float64`` for dtypes GDAL cannot represent directly
    (e.g. on older GDAL builds lacking Int64 support).
    """
    np_dtype = np.dtype(dtype)
    code = gdal_array.NumericTypeCodeToGDALTypeCode(np_dtype.type)
    if code is None:
        code = gdal.GDT_Float64
    return int(code)


def wkt_of(crs: CRSLike) -> str:
    """Return the WKT for any CRS-like value, or ``""`` for ``None``."""
    if crs is None:
        return ""
    return CRS.from_user_input(crs).to_wkt()


def mem_dataset_from_array(
    array: np.ndarray[Any, np.dtype[Any]],
    transform: Any,
    crs: CRSLike,
    nodata: float | None,
) -> "gdal.Dataset":
    """Create a single-band in-memory GDAL dataset wrapping ``array``.

    Args:
        array: A 2-D numpy array (rows, cols).
        transform: The array's affine transform (shim ``Affine``).
        crs: The array's CRS (any CRS-like value or ``None``).
        nodata: Nodata sentinel to record on the band, or ``None``.

    Returns:
        An open in-memory ``gdal.Dataset`` with the array written to band 1.
    """
    arr = np.ascontiguousarray(array)
    rows, cols = arr.shape
    gtype = np_to_gdal_dtype(arr.dtype)
    dataset = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gtype)
    dataset.SetGeoTransform(transform.to_gdal())
    wkt = wkt_of(crs)
    if wkt:
        dataset.SetProjection(wkt)
    band = dataset.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(float(nodata))
    band.WriteArray(arr)
    band.FlushCache()
    return dataset
