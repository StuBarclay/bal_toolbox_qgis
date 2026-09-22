"""Historical fire-history rasterisation for risk-context analysis.

This module supports the toolbox's *analysis* layer, which is entirely
separate from the AS 3959 bushfire attack level (BAL) calculation and
never modifies a BAL rating. AS 3959 is a prescriptive, design-condition
standard that deliberately assumes mature steady-state fuel loads and
ignores fire history; nothing here feeds back into that calculation. The
outputs are context for prioritisation, vegetation management and
validation only.

Given a fire-history polygon layer carrying a fire-year attribute, this
rasterises two derived layers onto a reference grid (typically the BAL
output grid):

* ``year_last_fire`` -- the most recent fire year touching each cell, or
  the grid nodata sentinel where no fire is recorded.
* ``fire_count`` -- the number of distinct fire polygons touching each
  cell (times burnt), ``0`` where no fire is recorded.

From ``year_last_fire`` a :func:`years_since_fire` helper derives the
time since the last fire relative to an analysis year, which the fuel-
recovery model (:mod:`bal_toolbox_qgis.balcore.fuel_recovery`) turns into a fuel-load
recovery estimate.

Polygon inputs are read as GeoJSON with the standard library (no
``fiona`` needed); other vector formats use the optional ``fiona``
dependency, matching :mod:`bal_toolbox_qgis.balcore.raster`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from bal_toolbox_qgis.balcore._rio.enums import MergeAlg
from bal_toolbox_qgis.balcore._rio.features import rasterize

from bal_toolbox_qgis.balcore import tables
from bal_toolbox_qgis.balcore.raster import RasterGrid, _is_geojson

logger = logging.getLogger(__name__)

#: Sentinel meaning "no fire recorded" in the ``year_last_fire`` raster.
NO_FIRE_YEAR: int = int(tables.NODATA)

#: Plausible bounds for a fire year. Values outside this range are treated
#: as unparseable (e.g. a feature id in the year field, or a truncated
#: string like "20191231" that is not actually a calendar year). The upper
#: bound is intentionally generous to allow the current/next fire season.
_MIN_FIRE_YEAR: int = 1900
_MAX_FIRE_YEAR: int = 2100


def _read_fire_features(
    path: Path,
    year_field: str,
) -> list[tuple[dict[str, object], int]]:
    """Read fire polygons and their fire year from a vector file.

    GeoJSON (``.geojson``/``.json``) is read natively with the standard
    library; other vector formats use the optional :mod:`fiona`
    dependency. Features whose ``year_field`` is missing or unparseable
    are skipped with a warning rather than aborting the run.

    Args:
        path: Path to a fire-history polygon vector file.
        year_field: Name of the property holding the fire year (an
            integer year, e.g. ``2019``, or a value ``int()`` accepts).

    Returns:
        A list of ``(geometry, fire_year)`` pairs in the file's own
        coordinate reference system.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ImportError: If a non-GeoJSON file is given and :mod:`fiona` is
            not installed.
        ValueError: If the file contains no usable fire polygon.
    """
    if not path.exists():
        raise FileNotFoundError(f"Fire-history file not found: {path}")

    if _is_geojson(path):
        pairs = _read_fire_geojson(path, year_field)
    else:
        pairs = _read_fire_fiona(path, year_field)

    if not pairs:
        raise ValueError(
            f"Fire-history file has no usable polygons with a '{year_field}' "
            f"year: {path}"
        )
    return pairs


def _coerce_year(value: object) -> int | None:
    """Coerce a raw attribute value to an integer fire year, or ``None``.

    Accepts an integer, a numeric string, or a date/datetime-like string
    whose leading four characters are the year (e.g. ``"2019-01-30"``).
    A parsed value is only accepted if it falls within a plausible fire-year
    range (:data:`_MIN_FIRE_YEAR`..:data:`_MAX_FIRE_YEAR`); anything else --
    a feature id in the year field, or a run-together digit string that is
    not a calendar year -- is rejected as ``None``.

    Args:
        value: The raw ``year_field`` value from a feature.

    Returns:
        The fire year as an ``int``, or ``None`` if it cannot be parsed or
        is out of the plausible range.
    """
    if isinstance(value, bool) or value is None:
        return None
    year: int | None = None
    if isinstance(value, int):
        year = value
    elif isinstance(value, float):
        year = int(value)
    else:
        text = str(value).strip()
        if len(text) >= 4 and text[:4].isdigit():
            year = int(text[:4])
        elif text.isdigit():
            year = int(text)
    if year is None or not (_MIN_FIRE_YEAR <= year <= _MAX_FIRE_YEAR):
        return None
    return year


def _read_fire_geojson(
    path: Path,
    year_field: str,
) -> list[tuple[dict[str, object], int]]:
    """Read ``(geometry, year)`` pairs from a GeoJSON fire-history file.

    Args:
        path: Path to a ``.geojson``/``.json`` file.
        year_field: Property name holding the fire year.

    Returns:
        A list of ``(geometry, fire_year)`` pairs; features without a
        parseable year are skipped.
    """
    import json

    with path.open("r", encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        # A bare geometry or single Feature carries no per-feature fire year,
        # so the fire-history analysis cannot use it; warn and treat as empty.
        logger.warning(
            "Fire-history file %s is not a FeatureCollection; no per-feature "
            "year is available, so fire years cannot be read.",
            path,
        )
        return []

    pairs: list[tuple[dict[str, object], int]] = []
    skipped = 0
    for feature in doc.get("features", []):
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        properties = feature.get("properties") or {}
        year = _coerce_year(
            properties.get(year_field) if isinstance(properties, dict) else None
        )
        if geometry is None or year is None:
            skipped += 1
            continue
        pairs.append((geometry, year))
    if skipped:
        logger.warning(
            "Skipped %d fire feature(s) with no geometry or unparseable '%s' year.",
            skipped,
            year_field,
        )
    return pairs


def _read_fire_fiona(
    path: Path,
    year_field: str,
) -> list[tuple[dict[str, object], int]]:
    """Read ``(geometry, year)`` pairs from a non-GeoJSON vector file.

    Args:
        path: Path to a vector file readable by :mod:`fiona`.
        year_field: Property name holding the fire year.

    Returns:
        A list of ``(geometry, fire_year)`` pairs; features without a
        parseable year are skipped.

    Raises:
        ImportError: If :mod:`fiona` is not installed.
    """
    try:
        from bal_toolbox_qgis.balcore._rio import _fiona as fiona
    except ImportError as err:  # pragma: no cover - exercised via message
        raise ImportError(
            "Reading a non-GeoJSON fire-history layer requires GDAL's Python "
            "bindings (osgeo), which ship with QGIS; or supply the layer as "
            "GeoJSON (.geojson)."
        ) from err

    pairs: list[tuple[dict[str, object], int]] = []
    skipped = 0
    with fiona.open(path) as layer:
        for feature in layer:
            geometry = feature["geometry"]
            year = _coerce_year(feature["properties"].get(year_field))
            if geometry is None or year is None:
                skipped += 1
                continue
            pairs.append((dict(geometry), year))
    if skipped:
        logger.warning(
            "Skipped %d fire feature(s) with no geometry or unparseable '%s' year.",
            skipped,
            year_field,
        )
    return pairs


def _reproject_pairs(
    pairs: list[tuple[dict[str, object], int]],
    source_crs: str | None,
    grid: RasterGrid,
) -> list[tuple[dict[str, object], int]]:
    """Reproject fire geometries into the reference grid's CRS.

    Args:
        pairs: ``(geometry, year)`` pairs in ``source_crs``.
        source_crs: CRS of the geometries, or ``None`` to assume they are
            already in the grid's CRS.
        grid: The reference grid whose CRS the geometries are warped to.

    Returns:
        The pairs with geometries in the grid's CRS (unchanged when the
        CRS already matches or either CRS is undefined).
    """
    if source_crs is None or grid.crs is None:
        return pairs
    from bal_toolbox_qgis.balcore._rio.crs import CRS
    from bal_toolbox_qgis.balcore._rio.warp import transform_geom

    source = CRS.from_user_input(source_crs)
    if source == grid.crs:
        return pairs
    return [
        (transform_geom(source, grid.crs, geometry), year) for geometry, year in pairs
    ]


def rasterise_fire_history(
    pairs: list[tuple[dict[str, object], int]],
    source_crs: str | None,
    grid: RasterGrid,
) -> tuple[NDArray[np.int_], NDArray[np.int_]]:
    """Rasterise fire polygons into year-last-fire and fire-count grids.

    Each polygon is burnt onto the reference grid with "all-touched"
    rasterisation, so every cell a polygon intersects is counted -- the
    same robustness choice as the zonal building assignment.

    Args:
        pairs: ``(geometry, fire_year)`` pairs (any CRS; reprojected to
            ``grid`` here).
        source_crs: CRS of the geometries, or ``None`` if already in the
            grid's CRS.
        grid: Reference grid to rasterise onto (typically the BAL grid).

    Returns:
        A tuple ``(year_last_fire, fire_count)``. ``year_last_fire`` holds
        the most recent fire year per cell, or :data:`NO_FIRE_YEAR` where
        no fire is recorded. ``fire_count`` holds the number of polygons
        touching each cell (``0`` where none).
    """
    located = _reproject_pairs(pairs, source_crs, grid)

    year_last = np.full(grid.shape, NO_FIRE_YEAR, dtype=np.int_)
    fire_count = np.zeros(grid.shape, dtype=np.int_)

    # fire_count: add 1 per polygon touching a cell (MergeAlg.add).
    count_layer = rasterize(
        ((geometry, 1) for geometry, _ in located),
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=True,
        merge_alg=MergeAlg.add,
        dtype=np.int32,
    )
    fire_count[:] = count_layer.astype(np.int_)

    # year_last_fire: burn each polygon's year, oldest first, so later
    # (more recent) years overwrite older ones -> the maximum per cell.
    for geometry, year in sorted(located, key=lambda pair: pair[1]):
        mask = rasterize(
            [(geometry, 1)],
            out_shape=grid.shape,
            transform=grid.transform,
            fill=0,
            all_touched=True,
            dtype=np.uint8,
        ).astype(bool)
        year_last[mask] = year

    logger.info(
        "Rasterised %d fire polygon(s); %d of %d cells have a recorded fire.",
        len(located),
        int((fire_count > 0).sum()),
        year_last.size,
    )
    return year_last, fire_count


def years_since_fire(
    year_last_fire: NDArray[np.int_],
    analysis_year: int,
) -> NDArray[np.float64]:
    """Years since the last recorded fire, relative to an analysis year.

    Args:
        year_last_fire: Most-recent fire-year raster (:data:`NO_FIRE_YEAR`
            where no fire is recorded), e.g. from
            :func:`rasterise_fire_history`.
        analysis_year: The reference (calendar) year to measure back from.

    Returns:
        A float raster of years since the last fire. Cells with no
        recorded fire are :data:`bal_toolbox_qgis.balcore.tables.NODATA`; a future or
        same-year fire is clamped to ``0``.
    """
    result = np.full(year_last_fire.shape, float(tables.NODATA), dtype=np.float64)
    burnt = year_last_fire != NO_FIRE_YEAR
    result[burnt] = np.maximum(0.0, analysis_year - year_last_fire[burnt])
    return result
