"""Core AS 3959:2018 Method 1 bushfire attack level (BAL) computation.

This module reimplements the spatial Method 1 algorithm of the original
Geoscience Australia ArcGIS toolbox using pure :mod:`numpy` array
operations, with no dependency on ``arcpy``. The per-pixel directional
search of the original implementation is fully vectorised: for each of
the eight compass directions and each discrete step away from the point
of interest, the contribution of every cell is evaluated simultaneously
across the whole raster, and the running per-cell maximum BAL is kept.

All distance tables are AS 3959:2018 (see :mod:`bal_toolbox_qgis.balcore.tables`).
"""

from __future__ import annotations

from typing import Final

import numpy as np
from numpy.typing import NDArray

from bal_toolbox_qgis.balcore import tables

#: Compass-aspect code for each search direction (matches reclassified
#: aspect raster: 1=N, 2=NE, ... 8=NW, 9=flat).
_DIRECTION_ASPECT: Final[dict[str, int]] = {
    "n": 1,
    "ne": 2,
    "e": 3,
    "se": 4,
    "s": 5,
    "sw": 6,
    "w": 7,
    "nw": 8,
}

#: Per-direction (row, col) unit step from the point of interest toward
#: the neighbour being assessed.
_DIRECTION_STEP: Final[dict[str, tuple[int, int]]] = {
    "n": (-1, 0),
    "ne": (-1, 1),
    "e": (0, 1),
    "se": (1, 1),
    "s": (1, 0),
    "sw": (1, -1),
    "w": (0, -1),
    "nw": (-1, -1),
}

#: Search directions, ordered as in the original toolbox.
DIRECTIONS: Final[tuple[str, ...]] = (
    "w",
    "e",
    "n",
    "s",
    "nw",
    "ne",
    "se",
    "sw",
)


def output_names(write_directions: bool) -> tuple[str, ...]:
    """Return the ordered BAL output raster keys for a run.

    Args:
        write_directions: If ``True``, include the eight per-direction
            outputs alongside the cell-wise maximum; otherwise only the
            maximum.

    Returns:
        ``("max",)`` or ``("max", *DIRECTIONS)``. These are the keys of
        the :func:`compute_bal` result that are written to disk.
    """
    return ("max", *DIRECTIONS) if write_directions else ("max",)


#: Maximum search distance from the point of interest (metres).
MAX_SEARCH_DISTANCE_M: Final[float] = 100.0

#: Diagonal cell-spacing multiplier (sqrt(2)).
_DIAGONAL_FACTOR: Final[float] = float(np.sqrt(2.0))


def validate_pixel_width(pixel_width: float) -> None:
    """Ensure the cell size is a usable positive, finite metre distance.

    A zero, negative, NaN or infinite ``pixel_width`` (e.g. from a
    degenerate or misread geotransform) would make the directional search
    step count ``MAX_SEARCH_DISTANCE_M / spacing`` non-finite and crash with
    an opaque ``OverflowError`` deep in the search loop.

    Args:
        pixel_width: Cell size in projection units (metres).

    Raises:
        ValueError: If ``pixel_width`` is not finite and > 0.
    """
    if not np.isfinite(pixel_width) or pixel_width <= 0.0:
        raise ValueError(
            f"pixel_width must be a positive, finite metre distance; got "
            f"{pixel_width!r}. Check the DEM geotransform and CRS."
        )


def build_limit_array(fdi: int) -> NDArray[np.float64]:
    """Build a dense distance-limit lookup array for one FDI value.

    Args:
        fdi: Fire Danger Index (one of :data:`tables.FDI_VALUES`).

    Returns:
        Array of shape ``(6, 9, 4)`` indexed by ``[slope_band,
        veg_class, limit]``. Slope band 0 and veg class 0 are unused
        padding so that the 1-based codes index directly. Missing
        ``(slope_band, veg_class)`` combinations are filled with
        ``inf`` so that any distance falls in the flame-zone class.

    Raises:
        KeyError: If ``fdi`` is not a recognised value.
    """
    if fdi not in tables.DIST_LIMITS:
        raise KeyError(f"Unsupported FDI {fdi!r}; expected one of {tables.FDI_VALUES}.")

    limits = np.full((6, 9, 4), np.inf, dtype=np.float64)
    for (slope_band, veg_class), values in tables.DIST_LIMITS[fdi].items():
        limits[slope_band, veg_class, :] = values
    return limits


def estimate_bal(
    veg: NDArray[np.int_],
    slope_in_aspect: NDArray[np.int_],
    distance_m: float,
    limit_array: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Estimate the BAL of every cell at a fixed horizontal distance.

    This is the vectorised equivalent of the original ``bal_esti``
    scalar function, evaluated across the whole raster at once.

    Args:
        veg: Reclassified vegetation classes (1-8) or
            :data:`tables.NODATA`.
        slope_in_aspect: Reclassified slope band for cells facing the
            search direction (1-6), :data:`tables.UPSLOPE_SENTINEL`
            for cells treated as upslope/flat, or :data:`tables.NODATA`.
        distance_m: Horizontal distance from the point of interest (m).
        limit_array: Distance-limit lookup from :func:`build_limit_array`.

    Returns:
        Array of BAL codes (``100`` flame zone, ``40``, ``29``,
        ``19``, ``12.5``) with :data:`tables.NODATA` where vegetation
        or slope is nodata, or where grass-type vegetation lies beyond
        its 50 m assessment limit. Cells assessed but below BAL-12.5
        remain :data:`tables.NODATA` here; :func:`compute_bal` promotes
        those that are valid sites to :data:`tables.BAL_LOW`.
    """
    nodata = tables.NODATA
    result = np.full(veg.shape, float(nodata), dtype=np.float64)

    valid = (veg != nodata) & (slope_in_aspect != nodata)
    if not valid.any():
        return result

    # Steep downslope (>20 degrees) is always flame zone (BAL-FZ).
    steep = valid & (slope_in_aspect == tables.SLOPE_DOWN_GT_20)
    result[steep] = tables.BAL_FZ

    # Remaining valid cells use the prescriptive distance tables.
    table_cells = valid & ~steep

    # Upslope sentinel and flat band share the upslope/flat table row.
    effective_band = slope_in_aspect.copy()
    effective_band[effective_band == tables.UPSLOPE_SENTINEL] = (
        tables.SLOPE_FLAT_UPSLOPE
    )

    # Clip indices so masked-out cells index valid padding (overwritten).
    safe_band = np.where(table_cells, effective_band, 1)
    safe_veg = np.where(table_cells, veg, 1)
    cell_limits = limit_array[safe_band, safe_veg]

    # Distance class = number of upper limits the distance meets/exceeds.
    distance_class = (distance_m >= cell_limits).sum(axis=-1)
    bal_codes = np.asarray(tables.BAL_BANDS, dtype=np.float64)[distance_class]
    result[table_cells] = bal_codes[table_cells]

    # Grass-type vegetation is assessed only out to 50 m (AS 3959:2018
    # Cl. 2.2.3.2): beyond that the site is below BAL-12.5 (no rating).
    if distance_m >= tables.GRASS_MAX_DISTANCE_M:
        is_grass = np.isin(veg, tuple(tables.GRASS_TYPE_CLASSES))
        result[is_grass] = float(nodata)

    return result


def _slope_facing_direction(
    slope_band: NDArray[np.int_],
    aspect_band: NDArray[np.int_],
    aspect_value: int,
) -> NDArray[np.int_]:
    """Return slope bands for cells facing a direction, else upslope.

    Cells whose reclassified aspect matches ``aspect_value`` keep their
    slope band; all other valid cells are treated as upslope/flat
    (:data:`tables.UPSLOPE_SENTINEL`); nodata cells are preserved.

    Args:
        slope_band: Reclassified slope band raster (1-6) or nodata.
        aspect_band: Reclassified aspect raster (1-9) or nodata.
        aspect_value: Aspect code identifying the search direction.

    Returns:
        Slope-in-aspect array (see :func:`estimate_bal`).
    """
    facing = np.full(slope_band.shape, tables.UPSLOPE_SENTINEL, dtype=slope_band.dtype)
    nodata_mask = slope_band == tables.NODATA
    facing[nodata_mask] = tables.NODATA
    same_aspect = aspect_band == aspect_value
    facing[same_aspect] = slope_band[same_aspect]
    return facing


def _shift_toward_poi(
    data: NDArray[np.float64],
    step: tuple[int, int],
    multiple: int,
) -> NDArray[np.float64]:
    """Shift an array so each cell sees its neighbour ``multiple`` steps away.

    Cells whose neighbour falls outside the raster are filled with
    :data:`tables.NODATA` so they contribute no BAL.

    Args:
        data: Source array (neighbour values to be gathered).
        step: Unit ``(row, col)`` step toward the neighbour.
        multiple: Number of steps (>= 1).

    Returns:
        Shifted array aligned to the point-of-interest grid.
    """
    row_shift = step[0] * multiple
    col_shift = step[1] * multiple
    shifted = np.full_like(data, float(tables.NODATA))

    rows, cols = data.shape
    src_r0 = max(0, row_shift)
    src_r1 = min(rows, rows + row_shift)
    src_c0 = max(0, col_shift)
    src_c1 = min(cols, cols + col_shift)
    if src_r0 >= src_r1 or src_c0 >= src_c1:
        return shifted

    dst_r0 = src_r0 - row_shift
    dst_r1 = src_r1 - row_shift
    dst_c0 = src_c0 - col_shift
    dst_c1 = src_c1 - col_shift
    shifted[dst_r0:dst_r1, dst_c0:dst_c1] = data[src_r0:src_r1, src_c0:src_c1]
    return shifted


def compute_direction(
    direction: str,
    veg: NDArray[np.int_],
    slope_band: NDArray[np.int_],
    aspect_band: NDArray[np.int_],
    pixel_width: float,
    limit_array: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Compute the maximum BAL contribution from one search direction.

    Args:
        direction: One of :data:`DIRECTIONS`.
        veg: Reclassified vegetation raster (1-8) or nodata.
        slope_band: Reclassified slope band raster (1-6) or nodata.
        aspect_band: Reclassified aspect raster (1-9) or nodata.
        pixel_width: Cell size in projection units (metres).
        limit_array: Distance-limit lookup from :func:`build_limit_array`.

    Returns:
        Per-cell maximum BAL looking in ``direction``, with
        :data:`tables.NODATA` where no rating applies.
    """
    aspect_value = _DIRECTION_ASPECT[direction]
    step = _DIRECTION_STEP[direction]
    spacing = pixel_width * (
        _DIAGONAL_FACTOR if direction in ("nw", "ne", "se", "sw") else 1.0
    )
    step_count = int(np.ceil(MAX_SEARCH_DISTANCE_M / spacing))

    facing = _slope_facing_direction(slope_band, aspect_band, aspect_value)
    veg_float = veg.astype(np.float64)
    facing_float = facing.astype(np.float64)

    result = np.full(veg.shape, float(tables.NODATA), dtype=np.float64)
    for s in range(1, step_count + 1):
        distance_m = (s - 0.5) * spacing
        neighbour_veg = _shift_toward_poi(veg_float, step, s).astype(np.int_)
        neighbour_slope = _shift_toward_poi(facing_float, step, s).astype(np.int_)
        contribution = estimate_bal(
            neighbour_veg, neighbour_slope, distance_m, limit_array
        )
        np.maximum(result, contribution, out=result)
    return result


def compute_bal(
    veg: NDArray[np.int_],
    slope_band: NDArray[np.int_],
    aspect_band: NDArray[np.int_],
    pixel_width: float,
    fdi: int,
) -> dict[str, NDArray[np.float64]]:
    """Compute per-direction and overall maximum BAL rasters.

    Args:
        veg: Reclassified vegetation raster (1-8) or nodata.
        slope_band: Reclassified slope band raster (1-6) or nodata.
        aspect_band: Reclassified aspect raster (1-9) or nodata.
        pixel_width: Cell size in projection units (metres).
        fdi: Fire Danger Index (one of :data:`tables.FDI_VALUES`).

    Returns:
        Mapping of each direction in :data:`DIRECTIONS` to its BAL
        raster, plus ``"max"`` for the cell-wise maximum across all
        directions. Output codes are :data:`tables.BAL_LOW` (0),
        ``12.5``, ``19``, ``29``, ``40`` and :data:`tables.BAL_FZ`
        (100, Flame Zone); cells outside the assessed area are
        :data:`tables.NODATA`.

    Raises:
        ValueError: If Tussock moorland (class 8) appears with an FDI
            other than 50, for which AS 3959:2018 has no table.
    """
    validate_pixel_width(pixel_width)
    if fdi != 50 and np.any(veg == tables.VEG_TUSSOCK_MOORLAND):
        raise ValueError(
            "Tussock moorland (vegetation class 8) is only tabulated for "
            "FDI 50 in AS 3959:2018; reclassify it or run with FDI 50."
        )

    limit_array = build_limit_array(fdi)
    outputs: dict[str, NDArray[np.float64]] = {}
    overall: NDArray[np.float64] | None = None
    for direction in DIRECTIONS:
        band = compute_direction(
            direction, veg, slope_band, aspect_band, pixel_width, limit_array
        )
        outputs[direction] = band
        overall = band if overall is None else np.maximum(overall, band)

    assert overall is not None  # DIRECTIONS is non-empty.
    outputs["max"] = overall

    # An assessable site is any cell with valid terrain (the DEM defines the
    # output grid, so a valid slope band means the cell is inside the assessed
    # area, regardless of its own vegetation). Such cells left unrated by the
    # directional search are below BAL-12.5, i.e. BAL-LOW -- distinct from
    # cells outside the assessed area, which stay NODATA.
    assessable = slope_band != tables.NODATA
    for raster in outputs.values():
        unrated = assessable & (raster == tables.NODATA)
        raster[unrated] = tables.BAL_LOW
    return outputs
