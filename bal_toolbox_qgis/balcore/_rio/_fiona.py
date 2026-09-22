"""Minimal ``fiona``-compatible vector reader backed by OGR (osgeo).

The core reads non-GeoJSON polygon layers (shapefile, GeoPackage, ...)
through a tiny slice of the fiona API: ``fiona.open(path)`` used as a
context manager, then ``layer.bounds``, ``layer.crs.to_string()`` and
iteration yielding feature mappings that expose ``"geometry"`` (a
GeoJSON-like dict) and ``"properties"`` (a field mapping). This module
reproduces exactly that surface using OGR, so no ``fiona`` dependency is
required -- OGR ships with every QGIS install.

Keeping this behind the same ``fiona`` name means the vendored core
modules stay byte-for-byte identical apart from their import line, and
all GDAL usage remains confined to the :mod:`_rio` package.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator

from osgeo import ogr

from .crs import CRS
from .errors import RasterioError

__all__ = ["open"]


class _CRSProxy:
    """Stand-in for fiona's CRS object, exposing ``to_string()``.

    Only the ``to_string()`` method and truthiness are consumed by the
    core (``layer.crs.to_string() if layer.crs else None``).
    """

    def __init__(self, wkt: str) -> None:
        self._crs = CRS.from_wkt(wkt)

    def to_string(self) -> str:
        return self._crs.to_string()

    def __bool__(self) -> bool:
        return True


class _VectorLayer:
    """A read-only OGR layer wrapper mirroring the used ``fiona`` layer API."""

    def __init__(self, path: str) -> None:
        self._ds = ogr.Open(path, 0)
        if self._ds is None:
            raise RasterioError(f"Could not open vector file: {path}")
        layer = self._ds.GetLayer(0)
        if layer is None:  # pragma: no cover - defensive
            raise RasterioError(f"Vector file has no layers: {path}")
        self._layer = layer

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        """Return ``(xmin, ymin, xmax, ymax)`` or ``None`` if empty.

        OGR's ``GetExtent`` yields ``(minx, maxx, miny, maxy)``; this is
        re-ordered to fiona's ``(minx, miny, maxx, maxy)`` convention.
        """
        if self._layer.GetFeatureCount() == 0:
            return None
        minx, maxx, miny, maxy = self._layer.GetExtent()
        return (float(minx), float(miny), float(maxx), float(maxy))

    @property
    def crs(self) -> _CRSProxy | None:
        srs = self._layer.GetSpatialRef()
        if srs is None:
            return None
        return _CRSProxy(srs.ExportToWkt())

    def __iter__(self) -> Iterator[dict[str, Any]]:
        self._layer.ResetReading()
        for feat in self._layer:
            geom = feat.GetGeometryRef()
            geometry: Any = json.loads(geom.ExportToJson()) if geom is not None else None
            properties: dict[str, Any] = {}
            for i in range(feat.GetFieldCount()):
                defn = feat.GetFieldDefnRef(i)
                properties[defn.GetName()] = feat.GetField(i)
            yield {"geometry": geometry, "properties": properties}

    def close(self) -> None:
        self._layer = None
        self._ds = None

    def __enter__(self) -> "_VectorLayer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open(path: str | os.PathLike[str]) -> _VectorLayer:  # noqa: A001 - mirror ``fiona.open``
    """Open a vector file for reading, mirroring :func:`fiona.open`."""
    return _VectorLayer(os.fspath(path))
