"""Building-level fire-history and fuel-recovery overlay (analysis layer).

This is the orchestration entry point for the toolbox's *analysis*
layer, which is entirely separate from the AS 3959 bushfire attack level
(BAL) calculation and never alters a BAL rating. It answers two
risk-context questions for a set of building footprints:

* Have these buildings been in past fires, and how recently / how often?
* How far has the fine fuel around them recovered since the last fire?

It samples, for each footprint, the worst BAL band (for cross-tabulation
only), the most recent fire year, the times burnt, the years since the
last fire, and the recovered fine-fuel fraction (where a vegetation-class
raster is supplied). The footprints are written to a new GeoJSON with
those attributes added *alongside* -- never replacing -- any existing
BAL attributes, and a BAL-band x time-since-fire summary cross-tab is
returned for reporting.

The analysis reuses the BAL output grid as its reference grid and the
same "all-touched" footprint sampling as :mod:`bal_toolbox_qgis.balcore.zonal`, so a
footprint between cell centres still captures the cells it sits on.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from bal_toolbox_qgis.balcore import _rio as rasterio
from numpy.typing import NDArray
from bal_toolbox_qgis.balcore._rio.errors import WindowError
from bal_toolbox_qgis.balcore._rio.features import geometry_mask
from bal_toolbox_qgis.balcore._rio.warp import transform_geom
from bal_toolbox_qgis.balcore._rio.windows import Window, from_bounds

from bal_toolbox_qgis.balcore import fire_history, fuel_recovery, tables
from bal_toolbox_qgis.balcore.raster import (
    RasterGrid,
    geojson_crs,
    matches_grid,
    read_polygon_bounds,
    read_raster,
    reproject_to_grid,
    set_geojson_crs,
)

logger = logging.getLogger(__name__)

#: Upper bounds (years) of the time-since-fire bins used in the summary
#: cross-tab; the final bin is open-ended ("20+").
_TIME_BINS: tuple[float, ...] = (5.0, 10.0, 20.0)
_TIME_BIN_LABELS: tuple[str, ...] = ("0-5", "5-10", "10-20", "20+", "no recorded fire")


@dataclass(frozen=True, slots=True)
class FootprintHistory:
    """Per-footprint fire-history and fuel-recovery summary.

    Attributes:
        bal_max: Worst BAL band touching the footprint, or ``None`` if no
            valid BAL cell (used only for the summary cross-tab).
        year_last_fire: Most recent fire year touching the footprint, or
            ``None`` if no recorded fire.
        times_burnt: Greatest fire count of any cell the footprint
            touches (``0`` if never burnt, ``None`` if the footprint
            covers no grid cell at all).
        years_since_fire: Years since the most recent fire, or ``None``.
        fuel_recovery_pct: Mean recovered fine-fuel percentage (0-100)
            over the footprint's burnt cells, or ``None`` if not computed.
    """

    bal_max: float | None
    year_last_fire: int | None
    times_burnt: int | None
    years_since_fire: float | None
    fuel_recovery_pct: float | None


def _footprint_window(
    geometry: dict[str, Any],
    grid_transform: rasterio.Affine,
    width: int,
    height: int,
) -> Window | None:
    """Return the clamped pixel window covering a geometry, or ``None``.

    Args:
        geometry: A geometry in the grid's CRS (GeoJSON mapping).
        grid_transform: The grid's affine transform.
        width: Grid width in pixels.
        height: Grid height in pixels.

    Returns:
        A :class:`rasterio.windows.Window` (padded one cell on the far
        edges so a small footprint still captures its cell), or ``None``
        if the footprint lies outside the grid.
    """
    from bal_toolbox_qgis.balcore._rio.features import bounds as feature_bounds

    try:
        minx, miny, maxx, maxy = feature_bounds(geometry)
        window = from_bounds(minx, miny, maxx, maxy, transform=grid_transform)
        window = window.round_offsets(op="floor").round_lengths(op="ceil")
    except (WindowError, ValueError, KeyError, IndexError, TypeError):
        # Degenerate or malformed geometry -> treat as off-grid.
        return None

    col_off = max(0, int(window.col_off))
    row_off = max(0, int(window.row_off))
    col_end = min(width, int(window.col_off) + int(window.width) + 1)
    row_end = min(height, int(window.row_off) + int(window.height) + 1)
    if col_end <= col_off or row_end <= row_off:
        return None
    return Window(col_off, row_off, col_end - col_off, row_end - row_off)


def _sample_footprint(
    geometry: dict[str, Any],
    grid: RasterGrid,
    layers: dict[str, NDArray[np.floating]],
) -> dict[str, NDArray[np.floating]]:
    """Return each layer's values for the cells a footprint touches.

    Args:
        geometry: Footprint geometry in the grid's CRS.
        grid: The reference grid the layers share.
        layers: Named full-grid rasters to sample.

    Returns:
        Mapping of layer name to the 1-D array of its values inside the
        footprint (empty arrays if the footprint covers no cell).
    """
    window = _footprint_window(geometry, grid.transform, grid.shape[1], grid.shape[0])
    if window is None:
        return {name: np.empty(0) for name in layers}

    from bal_toolbox_qgis.balcore._rio.windows import transform as window_transform_fn

    row_off, col_off = int(window.row_off), int(window.col_off)
    rows, cols = int(window.height), int(window.width)
    win_transform = window_transform_fn(window, grid.transform)
    inside = geometry_mask(
        [geometry],
        out_shape=(rows, cols),
        transform=win_transform,
        invert=True,
        all_touched=True,
    )
    sampled: dict[str, NDArray[np.floating]] = {}
    for name, layer in layers.items():
        block = layer[row_off : row_off + rows, col_off : col_off + cols]
        sampled[name] = block[inside]
    return sampled


def _summarise_footprint(
    sampled: dict[str, NDArray[np.floating]],
    has_recovery: bool,
) -> FootprintHistory:
    """Reduce a footprint's sampled cells to a :class:`FootprintHistory`.

    Args:
        sampled: Per-layer value arrays from :func:`_sample_footprint`
            (keys ``bal``, ``year``, ``count``, ``ysf`` and, when
            available, ``recovery``).
        has_recovery: Whether a fuel-recovery layer was sampled.

    Returns:
        The reduced :class:`FootprintHistory`.
    """
    bal_vals = sampled["bal"]
    valid_bal = bal_vals[bal_vals != tables.NODATA]
    bal_max = float(np.max(valid_bal)) if valid_bal.size else None

    years = sampled["year"]
    burnt_years = years[years != fire_history.NO_FIRE_YEAR]
    year_last = int(np.max(burnt_years)) if burnt_years.size else None

    counts = sampled["count"]
    # No sampled cells at all (footprint off the grid) is "unknown" (None),
    # distinct from a footprint that sits on the grid but never burnt (0).
    times_burnt = int(np.max(counts)) if counts.size else None

    ysf_vals = sampled["ysf"]
    valid_ysf = ysf_vals[ysf_vals != tables.NODATA]
    years_since = float(np.min(valid_ysf)) if valid_ysf.size else None

    recovery_pct: float | None = None
    if has_recovery:
        rec = sampled["recovery"]
        valid_rec = rec[rec != tables.NODATA]
        if valid_rec.size:
            recovery_pct = round(float(np.mean(valid_rec)) * 100.0, 1)

    return FootprintHistory(
        bal_max=bal_max,
        year_last_fire=year_last,
        times_burnt=times_burnt,
        years_since_fire=years_since,
        fuel_recovery_pct=recovery_pct,
    )


def _time_bin_label(years_since: float | None) -> str:
    """Return the time-since-fire bin label for a years-since value."""
    if years_since is None:
        return _TIME_BIN_LABELS[-1]
    for index, upper in enumerate(_TIME_BINS):
        if years_since < upper:
            return _TIME_BIN_LABELS[index]
    return _TIME_BIN_LABELS[len(_TIME_BINS)]


def _bal_label(bal_max: float | None) -> str:
    """Return a readable BAL-band label for a numeric band value."""
    if bal_max is None:
        return "unrated"
    if bal_max == tables.BAL_FZ:
        return "BAL-FZ"
    if bal_max == tables.BAL_LOW:
        return "BAL-LOW"
    return f"BAL-{bal_max:g}"


def _load_grid_layers(
    bal_raster: Path,
    fire_layer: Path,
    year_field: str,
    analysis_year: int,
    veg_class_raster: Path | None,
) -> tuple[RasterGrid, dict[str, NDArray[np.floating]], bool]:
    """Read the BAL grid and build all sampled analysis layers on it.

    Args:
        bal_raster: Path to the BAL GeoTIFF defining the reference grid.
        fire_layer: Path to the fire-history polygon layer.
        year_field: Fire-year property name in the fire layer.
        analysis_year: Reference year for years-since-fire.
        veg_class_raster: Optional reclassified vegetation-class raster
            for fuel recovery; ``None`` skips fuel recovery.

    Returns:
        ``(grid, layers, has_recovery)`` where ``layers`` maps
        ``bal``/``year``/``count``/``ysf`` (and ``recovery``) to full-grid
        float arrays.

    Raises:
        FileNotFoundError: If the BAL raster does not exist.
    """
    bal_data, grid = read_raster(bal_raster)

    pairs = fire_history._read_fire_features(fire_layer, year_field)
    _, fire_crs = read_polygon_bounds(fire_layer)
    year_last, fire_count = fire_history.rasterise_fire_history(pairs, fire_crs, grid)
    ysf = fire_history.years_since_fire(year_last, analysis_year)

    layers: dict[str, NDArray[np.floating]] = {
        "bal": bal_data,
        "year": year_last.astype(np.float64),
        "count": fire_count.astype(np.float64),
        "ysf": ysf,
    }

    has_recovery = veg_class_raster is not None
    if veg_class_raster is not None:
        veg_data, veg_grid = read_raster(veg_class_raster)
        if not matches_grid(veg_grid, grid):
            logger.info(
                "Vegetation-class raster grid differs from the BAL grid; "
                "reprojecting (nearest) for fuel recovery."
            )
            veg_data = reproject_to_grid(veg_data, veg_grid, grid)
        layers["recovery"] = fuel_recovery.recovery_fraction_raster(
            veg_data.astype(np.int_), ysf
        )
    return grid, layers, has_recovery


def analyse_history(
    bal_raster: Path,
    footprints: Path,
    fire_layer: Path,
    output: Path,
    year_field: str = "year",
    analysis_year: int | None = None,
    veg_class_raster: Path | None = None,
    overwrite: bool = False,
    output_crs: str | None = None,
) -> dict[str, dict[str, int]]:
    """Annotate footprints with fire history and fuel recovery.

    Each output feature keeps its original geometry and properties, with
    these attributes added (alongside, never replacing, any BAL
    attributes): ``year_last_fire``, ``times_burnt``, ``years_since_fire``
    and, when ``veg_class_raster`` is given, ``fuel_recovery_pct``.

    Args:
        bal_raster: BAL GeoTIFF defining the reference grid (and the
            ``bal_max`` band used only for the summary cross-tab).
        footprints: GeoJSON FeatureCollection of building/parcel polygons.
        fire_layer: Fire-history polygon layer with a fire-year attribute.
        output: Path to write the annotated GeoJSON to.
        year_field: Fire-year property name in ``fire_layer``.
        analysis_year: Reference year for years-since-fire; defaults to
            the current UTC year.
        veg_class_raster: Optional reclassified AS 3959 vegetation-class
            raster (e.g. the ``vegetation_class.tif`` QA output) enabling
            the fuel-recovery estimate; omitted skips it.
        overwrite: Replace ``output`` if it exists; otherwise raise.
        output_crs: Coordinate reference system for the written GeoJSON
            (e.g. ``"EPSG:4283"``). ``None`` (the default) keeps each
            footprint in its input CRS.

    Returns:
        A nested summary cross-tab ``{bal_label: {time_bin_label: count}}``
        of footprint counts.

    Raises:
        FileNotFoundError: If an input file is missing.
        FileExistsError: If ``output`` exists and ``overwrite`` is False.
        ValueError: If inputs are malformed.
    """
    if not bal_raster.exists():
        raise FileNotFoundError(f"BAL raster not found: {bal_raster}")
    if not footprints.exists():
        raise FileNotFoundError(f"Footprint file not found: {footprints}")
    if output.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output}. Re-run with overwrite enabled "
            "(--overwrite) to replace it."
        )
    year = analysis_year if analysis_year is not None else _current_year()

    grid, layers, has_recovery = _load_grid_layers(
        bal_raster, fire_layer, year_field, year, veg_class_raster
    )

    with footprints.open("r", encoding="utf-8") as handle:
        doc: dict[str, Any] = json.load(handle)
    if doc.get("type") != "FeatureCollection":
        raise ValueError(
            f"Footprint file must be a GeoJSON FeatureCollection: {footprints}"
        )
    src_crs = geojson_crs(doc)
    features = doc.get("features", [])
    logger.info(
        "Analysing fire history for %d footprint(s) against %s",
        len(features),
        fire_layer,
    )

    summary: dict[str, dict[str, int]] = {}
    grid_crs = grid.crs.to_string() if grid.crs is not None else None
    reproject_out = output_crs is not None and output_crs != src_crs
    for feature in features:
        geometry = feature.get("geometry")
        props = feature.setdefault("properties", {})
        if geometry is None:
            _write_null_history(props, has_recovery)
            continue
        geom = (
            transform_geom(src_crs, grid_crs, geometry)
            if grid_crs is not None
            else geometry
        )
        sampled = _sample_footprint(geom, grid, layers)
        history = _summarise_footprint(sampled, has_recovery)
        _write_history_props(props, history, has_recovery)
        _tally(summary, history)
        if reproject_out:
            feature["geometry"] = transform_geom(src_crs, output_crs, geometry)

    if reproject_out:
        assert output_crs is not None  # reproject_out implies it is set
        set_geojson_crs(doc, output_crs)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(doc, handle)
        handle.write("\n")
    burnt = sum(1 for f in features if f.get("properties", {}).get("times_burnt", 0))
    logger.info(
        "Wrote %s (%d of %d footprints intersect a recorded fire)",
        output,
        burnt,
        len(features),
    )
    return summary


def _current_year() -> int:
    """Return the current UTC calendar year."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).year


def _write_null_history(props: dict[str, Any], has_recovery: bool) -> None:
    """Write null history attributes for a geometry-less feature."""
    props["year_last_fire"] = None
    props["times_burnt"] = None
    props["years_since_fire"] = None
    if has_recovery:
        props["fuel_recovery_pct"] = None


def _write_history_props(
    props: dict[str, Any],
    history: FootprintHistory,
    has_recovery: bool,
) -> None:
    """Write a footprint's history attributes onto its properties."""
    props["year_last_fire"] = history.year_last_fire
    props["times_burnt"] = history.times_burnt
    props["years_since_fire"] = history.years_since_fire
    if has_recovery:
        props["fuel_recovery_pct"] = history.fuel_recovery_pct


def _tally(summary: dict[str, dict[str, int]], history: FootprintHistory) -> None:
    """Increment the BAL-band x time-since-fire cross-tab for a footprint."""
    bal_label = _bal_label(history.bal_max)
    time_label = _time_bin_label(history.years_since_fire)
    row = summary.setdefault(bal_label, Counter())
    row[time_label] += 1


def format_summary(summary: dict[str, dict[str, int]]) -> str:
    """Render the cross-tab as a fixed-width text table.

    Args:
        summary: The nested ``{bal_label: {time_bin: count}}`` mapping
            returned by :func:`analyse_history`.

    Returns:
        A human-readable table string (BAL bands as rows, time-since-fire
        bins as columns), with row and column totals.
    """
    bal_order = [
        "BAL-FZ",
        "BAL-40",
        "BAL-29",
        "BAL-19",
        "BAL-12.5",
        "BAL-LOW",
        "unrated",
    ]
    rows = [b for b in bal_order if b in summary]
    rows += [b for b in summary if b not in bal_order]

    header = f"{'BAL band':<10}" + "".join(f"{lbl:>13}" for lbl in _TIME_BIN_LABELS)
    header += f"{'total':>8}"
    lines = [header, "-" * len(header)]
    col_totals = dict.fromkeys(_TIME_BIN_LABELS, 0)
    for bal in rows:
        counts = summary[bal]
        cells = [int(counts.get(lbl, 0)) for lbl in _TIME_BIN_LABELS]
        for lbl, value in zip(_TIME_BIN_LABELS, cells, strict=True):
            col_totals[lbl] += value
        line = f"{bal:<10}" + "".join(f"{c:>13d}" for c in cells)
        line += f"{sum(cells):>8d}"
        lines.append(line)
    total_line = f"{'total':<10}" + "".join(
        f"{col_totals[lbl]:>13d}" for lbl in _TIME_BIN_LABELS
    )
    total_line += f"{sum(col_totals.values()):>8d}"
    lines.append("-" * len(header))
    lines.append(total_line)
    return "\n".join(lines)
