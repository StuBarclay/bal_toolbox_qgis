"""bal_toolbox_qgis.balcore -- AS 3959:2018 Method 1 bushfire attack level calculator.

A standalone, open-source port of the Geoscience Australia BAL ArcGIS
toolbox. It computes the Bushfire Attack Level (BAL) over an area of
interest from a digital elevation model and a vegetation raster, using
the prescriptive Method 1 distance tables of AS 3959:2018. The original
``arcpy`` dependency has been replaced with :mod:`rasterio`,
:mod:`numpy` and :mod:`scipy`.

For programmatic use, :func:`reclassify_mvg` is the convenience entry
point for reclassifying an NVIS Major Vegetation Group raster into the
AS 3959:2018 vegetation classes using the canonical mapping; see
:mod:`bal_toolbox_qgis.balcore.nvis`.
"""

from __future__ import annotations

from bal_toolbox_qgis.balcore.nvis import reclassify_mvg

__version__ = "1.1.0"
__all__ = ["__version__", "reclassify_mvg"]
