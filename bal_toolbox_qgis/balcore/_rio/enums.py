"""Enumerations mirroring the subset of :mod:`rasterio.enums` used.

Only :class:`Resampling` and :class:`MergeAlg` are needed by the vendored
core. Each member carries the corresponding GDAL constant as its value so
the warp/rasterize shims can pass it straight through to GDAL.
"""

from __future__ import annotations

import enum

from osgeo import gdalconst


class Resampling(enum.Enum):
    """Resampling algorithms, valued by their GDAL ``GRA_*`` constant.

    The core only ever uses :attr:`nearest` and :attr:`bilinear`, but the
    common set is provided for completeness and API parity.
    """

    nearest = gdalconst.GRA_NearestNeighbour
    bilinear = gdalconst.GRA_Bilinear
    cubic = gdalconst.GRA_Cubic
    cubic_spline = gdalconst.GRA_CubicSpline
    lanczos = gdalconst.GRA_Lanczos
    average = gdalconst.GRA_Average
    mode = gdalconst.GRA_Mode
    max = getattr(gdalconst, "GRA_Max", gdalconst.GRA_NearestNeighbour)
    min = getattr(gdalconst, "GRA_Min", gdalconst.GRA_NearestNeighbour)
    med = getattr(gdalconst, "GRA_Med", gdalconst.GRA_NearestNeighbour)


class MergeAlg(enum.Enum):
    """Rasterisation merge algorithms.

    ``replace`` overwrites touched pixels with the burn value (GDAL's
    default); ``add`` accumulates burn values, which the fire-history
    layer relies on to count overlapping fire polygons per cell.
    """

    replace = "REPLACE"
    add = "ADD"
