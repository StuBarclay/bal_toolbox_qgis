"""Slope, aspect and vegetation reclassification.

These routines reproduce the spatial-analyst replacements in the
original toolbox (slope/aspect from a DEM, and remap-based
reclassification) using :mod:`numpy`, with no ``arcpy`` dependency.
The slope and aspect formulation matches the centred-difference
gradient scheme of the original ``cal_slope_aspect``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from bal_toolbox_qgis.balcore import tables

#: Radians per degree.
_RADIANS_PER_DEGREE: float = np.pi / 180.0

#: Aspect reclassification breakpoints (compass degrees) to codes 1-8,
#: with north wrapping across 337.5-360 and 0-22.5 to code 1.
_ASPECT_BREAKS: tuple[float, ...] = (
    22.5,
    67.5,
    112.5,
    157.5,
    202.5,
    247.5,
    292.5,
    337.5,
)

#: Slope reclassification upper bounds (degrees) to bands 1-6. The final
#: band (steeper than 20 degrees) is open-ended.
_SLOPE_UPPER_BOUNDS: tuple[float, ...] = (0.0, 5.0, 10.0, 15.0, 20.0)

#: Slopes at or below this (degrees) are treated as flat/upslope (band 1),
#: absorbing floating-point roughness in DEM-derived slope.
_FLAT_SLOPE_EPS_DEG: float = 1e-3


def _fill_nodata_edges(elevation: NDArray[np.float64]) -> NDArray[np.float64]:
    """Propagate valid elevations into adjacent nodata cells.

    This mirrors the original toolbox behaviour so that centred-
    difference gradients near the data boundary remain finite. The fill
    is one cell deep in each of the four cardinal sweep directions.

    Args:
        elevation: Elevation array with :data:`tables.NODATA` sentinels.

    Returns:
        A copy of ``elevation`` with edge nodata cells filled.
    """
    filled = elevation.copy()
    nodata = tables.NODATA

    for axis in (0, 1):
        for reverse in (False, True):
            arr = np.flip(filled, axis=axis) if reverse else filled
            shifted = np.roll(arr, 1, axis=axis)
            if axis == 0:
                shifted[0, :] = nodata
            else:
                shifted[:, 0] = nodata
            gap = (arr == nodata) & (shifted != nodata)
            arr[gap] = shifted[gap]
            filled = np.flip(arr, axis=axis) if reverse else arr
    return filled


def slope_aspect(
    elevation: NDArray[np.float64],
    pixel_width: float,
    pixel_height: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Derive slope (degrees) and aspect (compass degrees) from a DEM.

    Args:
        elevation: DEM array with :data:`tables.NODATA` sentinels.
        pixel_width: Cell width in projection units (metres).
        pixel_height: Cell height in projection units (metres).

    Returns:
        A tuple ``(slope_degrees, aspect_degrees)``; nodata cells in the
        input are nodata in both outputs.
    """
    nodata = tables.NODATA
    mask = elevation == nodata
    filled = _fill_nodata_edges(elevation)

    # Interior nodata holes (not reached by the one-cell edge fill) would
    # otherwise be differenced against the -99 sentinel, producing a near-
    # vertical false slope in every neighbouring cell. Route them through
    # NaN so np.gradient propagates NaN one cell into the hole's neighbours;
    # those cells are then masked to nodata below rather than rated BAL-FZ.
    interior_hole = filled == nodata
    if interior_hole.any():
        filled = np.where(interior_hole, np.nan, filled)

    # np.gradient returns derivatives in axis order: axis 0 (rows, north-
    # south) first, then axis 1 (columns, east-west), and applies the
    # spacings in that same order. dz_dx is the east-west (column) derivative
    # and dz_dy the north-south (row) derivative.
    dz_dy, dz_dx = np.gradient(filled, pixel_height, pixel_width)

    slope = np.arctan(np.hypot(dz_dx, dz_dy)) / _RADIANS_PER_DEGREE
    aspect = np.mod(450.0 - np.arctan2(dz_dy, -dz_dx) / _RADIANS_PER_DEGREE, 360.0)

    # Cells whose gradient touched an interior hole (or were nodata to begin
    # with) are not assessable terrain.
    out_mask = mask | ~np.isfinite(slope)
    slope[out_mask] = nodata
    aspect[out_mask] = nodata
    return slope, aspect


def reclassify_aspect(aspect: NDArray[np.float64]) -> NDArray[np.int_]:
    """Reclassify aspect degrees into compass codes 1-8 (9 = flat).

    Args:
        aspect: Aspect raster in compass degrees, or :data:`tables.NODATA`.

    Returns:
        Integer aspect-band raster (codes 1-8, ``9`` for flat/undefined,
        :data:`tables.NODATA` preserved).
    """
    nodata = tables.NODATA
    result = np.full(aspect.shape, 9, dtype=np.int_)
    # np.digitize maps degrees to 0..8; codes 0 and 8 are both north (1).
    codes = np.digitize(aspect, _ASPECT_BREAKS)
    codes = np.where(codes >= 8, 0, codes) + 1
    valid = aspect != nodata
    flat = aspect < 0  # original encodes flat aspect as negative.
    result[valid] = codes[valid]
    result[flat & valid] = 9
    result[~valid] = nodata
    return result


def reclassify_slope(slope: NDArray[np.float64]) -> NDArray[np.int_]:
    """Reclassify slope degrees into bands 1-6.

    Band 1 is flat (0 degrees); bands 2-5 cover the >0-5, >5-10, >10-15
    and >15-20 degree ranges; band 6 is steeper than 20 degrees. The
    bands are minimum-exclusive and maximum-inclusive, matching the
    original toolbox.

    Args:
        slope: Slope raster in degrees, or :data:`tables.NODATA`.

    Returns:
        Integer slope-band raster (1-6, :data:`tables.NODATA` preserved).
    """
    nodata = tables.NODATA
    result = np.full(slope.shape, tables.SLOPE_DOWN_GT_20, dtype=np.int_)
    # Treat sub-milli-degree slopes as flat: real DEMs carry floating-point
    # roughness, so an exact == 0.0 test would misclass genuinely flat ground
    # (e.g. 1e-9 deg) as downslope band 2.
    result[slope <= _FLAT_SLOPE_EPS_DEG] = tables.SLOPE_FLAT_UPSLOPE
    for band, upper in enumerate(_SLOPE_UPPER_BOUNDS[1:], start=2):
        lower = _SLOPE_UPPER_BOUNDS[band - 2]
        result[(slope > lower) & (slope <= upper)] = band
    result[slope == nodata] = nodata
    return result


def reclassify_vegetation(
    veg: NDArray[np.floating],
    remap: Sequence[tuple[float, float, int]],
) -> NDArray[np.int_]:
    """Reclassify raw vegetation values into AS 3959:2018 classes 1-8.

    Args:
        veg: Raw vegetation raster.
        remap: Sequence of ``(low, high, class_code)`` rules; a value
            ``v`` is assigned ``class_code`` when ``low <= v <= high``.
            Later rules win on overlap, matching the original remap order.

    Returns:
        Integer vegetation-class raster (1-8, :data:`tables.NODATA`
        where no rule matches).

    Raises:
        ValueError: If a remap class code is outside 1-8.
    """
    nodata = tables.NODATA
    result = np.full(veg.shape, nodata, dtype=np.int_)
    for low, high, class_code in remap:
        if class_code not in tables.VEG_CLASSES:
            raise ValueError(f"Vegetation class {class_code} outside valid range 1-8.")
        result[(veg >= low) & (veg <= high)] = class_code
    return result
