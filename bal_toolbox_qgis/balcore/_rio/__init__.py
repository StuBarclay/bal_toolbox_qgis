"""A minimal, GDAL-backed re-implementation of the rasterio API surface.

The vendored ``bal_toolbox`` compute core was written against rasterio.
QGIS ships GDAL (``osgeo.gdal/ogr/osr``) with every install but not
rasterio, so this package reproduces *exactly* the subset of the rasterio
API the core touches, backed entirely by GDAL. The core's import lines are
mechanically rewritten to point here (``import rasterio`` ->
``from bal_toolbox_qgis.balcore import _rio as rasterio``); every other
line of the core is byte-identical to the upstream project.

Because the shim mirrors the real API, an equivalence test can drive both
this package and genuine rasterio through the same calls and assert
identical results -- see ``tests/test_rio_shim_equivalence.py``.

Exposed to match rasterio:

* ``rasterio.open`` and ``rasterio.Affine`` at the top level;
* submodules ``crs``, ``enums``, ``errors``, ``features``, ``io``,
  ``transform``, ``warp`` and ``windows``.
"""

from __future__ import annotations

from . import crs, enums, errors, features, io, transform, warp, windows
from .affine import Affine
from .io import open

__all__ = [
    "Affine",
    "open",
    "crs",
    "enums",
    "errors",
    "features",
    "io",
    "transform",
    "warp",
    "windows",
]
