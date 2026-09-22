"""Dataset objects mirroring the subset of :mod:`rasterio.io` / ``rasterio.open``.

``rasterio.open(path)`` returns a reader; ``rasterio.open(path, "w",
**profile)`` returns a writer. Both are context managers. The core also
references the type ``rasterio.io.DatasetReader`` in annotations, aliased
here to :class:`RasterDataset`.

Read paths accept GDAL dataset descriptors as well as plain file paths,
which is what lets :mod:`bal_toolbox_qgis.balcore.dem_source` open the
``<GDAL_WCS>`` XML descriptor it builds for the national SRTM service.
"""

from __future__ import annotations

import os
from typing import Any, Literal, overload

import numpy as np
from osgeo import gdal

from ._util import np_to_gdal_dtype, wkt_of
from .affine import Affine
from .crs import CRS
from .errors import RasterioError
from .windows import Window
from .windows import transform as _window_transform

__all__ = ["open", "DatasetReader", "RasterDataset", "RasterWriter"]


class RasterDataset:
    """A read-only single-purpose wrapper over a ``gdal.Dataset``.

    Exposes the small surface the core reads: ``width``, ``height``,
    ``transform``, ``crs``, ``nodata``, ``bounds``, ``read`` and
    ``window_transform``.
    """

    def __init__(self, source: str) -> None:
        self._ds = gdal.Open(str(source), gdal.GA_ReadOnly)
        if self._ds is None:
            raise RasterioError(f"Could not open raster dataset: {source}")
        self._gt = self._ds.GetGeoTransform()

    @property
    def width(self) -> int:
        return int(self._ds.RasterXSize)

    @property
    def height(self) -> int:
        return int(self._ds.RasterYSize)

    @property
    def count(self) -> int:
        return int(self._ds.RasterCount)

    @property
    def transform(self) -> Affine:
        return Affine.from_gdal(*self._gt)

    @property
    def crs(self) -> CRS | None:
        wkt = self._ds.GetProjection()
        if not wkt:
            return None
        return CRS.from_wkt(wkt)

    @property
    def nodata(self) -> float | None:
        value = self._ds.GetRasterBand(1).GetNoDataValue()
        return None if value is None else float(value)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        left, top = self.transform * (0.0, 0.0)
        right, bottom = self.transform * (float(self.width), float(self.height))
        return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))

    def read(
        self, indexes: int = 1, window: Window | None = None
    ) -> np.ndarray[Any, np.dtype[Any]]:
        """Read a band (optionally a window) as a numpy array."""
        band = self._ds.GetRasterBand(int(indexes))
        if window is None:
            array = band.ReadAsArray()
        else:
            xoff = int(window.col_off)
            yoff = int(window.row_off)
            xsize = int(window.width)
            ysize = int(window.height)
            array = band.ReadAsArray(xoff, yoff, xsize, ysize)
        if array is None:
            raise RasterioError("Failed to read raster band data")
        return np.asarray(array)

    def window_transform(self, window: Window) -> Affine:
        """Return the affine transform for ``window``'s own pixel grid."""
        return _window_transform(window, self.transform)

    def close(self) -> None:
        self._ds = None

    def __enter__(self) -> "RasterDataset":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


#: The core annotates parameters as ``rasterio.io.DatasetReader``.
DatasetReader = RasterDataset


class RasterWriter:
    """A single-band raster writer backing ``rasterio.open(path, "w", ...)``."""

    def __init__(self, path: str, profile: dict[str, Any]) -> None:
        self.path = str(path)
        self.profile = profile
        driver_name = profile.get("driver", "GTiff")
        driver = gdal.GetDriverByName(driver_name)
        if driver is None:
            raise RasterioError(f"Unknown GDAL driver: {driver_name}")

        width = int(profile["width"])
        height = int(profile["height"])
        count = int(profile.get("count", 1))
        np_dtype = np.dtype(profile["dtype"])

        creation_options: list[str] = []
        compress = profile.get("compress")
        if compress:
            creation_options.append(f"COMPRESS={str(compress).upper()}")

        self._ds = driver.Create(
            self.path,
            width,
            height,
            count,
            np_to_gdal_dtype(np_dtype),
            options=creation_options,
        )
        if self._ds is None:
            raise RasterioError(f"Could not create raster: {self.path}")

        transform = profile.get("transform")
        if transform is not None:
            self._ds.SetGeoTransform(transform.to_gdal())
        crs = profile.get("crs")
        if crs is not None:
            wkt = wkt_of(crs)
            if wkt:
                self._ds.SetProjection(wkt)

        self._nodata = profile.get("nodata")
        self._np_dtype = np_dtype

    def write(
        self, array: np.ndarray[Any, np.dtype[Any]], indexes: int = 1
    ) -> None:
        """Write a 2-D array into the given band (1-based)."""
        band = self._ds.GetRasterBand(int(indexes))
        if self._nodata is not None:
            band.SetNoDataValue(float(self._nodata))
        band.WriteArray(np.asarray(array).astype(self._np_dtype))
        band.FlushCache()

    def close(self) -> None:
        if self._ds is not None:
            self._ds.FlushCache()
            self._ds = None

    def __enter__(self) -> "RasterWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


@overload
def open(
    path: str | os.PathLike[str], mode: Literal["r"] = ..., **profile: Any
) -> RasterDataset: ...


@overload
def open(
    path: str | os.PathLike[str], mode: Literal["w", "w+"], **profile: Any
) -> RasterWriter: ...


def open(
    path: str | os.PathLike[str], mode: str = "r", **profile: Any
) -> RasterDataset | RasterWriter:
    """Open a raster for reading or writing, mirroring ``rasterio.open``.

    Overloaded on ``mode`` so callers get the concrete reader/writer type
    (matching ``rasterio.open``): ``open(path)`` narrows to
    :class:`RasterDataset`, ``open(path, "w", ...)`` to :class:`RasterWriter`.

    Args:
        path: A file path or GDAL dataset descriptor string.
        mode: ``"r"`` (read, the default) or ``"w"``/``"w+"`` (create).
        **profile: For write modes, the raster profile (``driver``,
            ``width``, ``height``, ``count``, ``dtype``, ``transform``,
            ``crs``, ``nodata``, ``compress``).

    Returns:
        A :class:`RasterDataset` (read) or :class:`RasterWriter` (write).
    """
    source = os.fspath(path)
    if mode == "r":
        return RasterDataset(source)
    if mode in ("w", "w+"):
        return RasterWriter(source, profile)
    raise ValueError(f"Unsupported open mode: {mode!r}")
