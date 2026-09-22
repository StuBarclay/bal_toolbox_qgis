"""Exception types mirroring the subset of :mod:`rasterio.errors` used.

The vendored ``bal_toolbox`` core catches these by name in a few places
(``CRSError`` when validating an output CRS, ``WindowError`` when a
footprint window is degenerate, ``RasterioError``/``OSError`` around the
WCS fetch). Keeping the class names and inheritance identical means the
core's ``except`` clauses behave exactly as they did against rasterio.
"""

from __future__ import annotations


class RasterioError(Exception):
    """Base class for errors raised by the GDAL-backed rasterio shim."""


class CRSError(RasterioError, ValueError):
    """Raised when a coordinate reference system cannot be parsed.

    Subclasses :class:`ValueError` to match :class:`rasterio.errors.CRSError`,
    so callers that catch either see identical behaviour.
    """


class WindowError(RasterioError):
    """Raised for an invalid or degenerate raster window."""
