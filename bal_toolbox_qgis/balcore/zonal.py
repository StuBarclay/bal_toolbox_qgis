"""Assign BAL ratings from a raster to polygon footprints.

Given a computed BAL raster and a set of polygons (e.g. building
outlines or cadastral parcels), this assigns each polygon two
attributes:

* ``bal_max`` -- the worst (numerically highest) BAL band of any raster
  cell touching the footprint. This is the conservative, assessment-
  relevant rating: a footprint is rated to its worst exposure.
* ``bal_dominant`` -- the most common (modal) BAL band by cell count
  across the footprint. Ties are broken toward the more severe band.

Footprints are sampled with "all-touched" rasterisation, so every cell a
polygon intersects is counted -- robust for small footprints that fall
between the ~30 m cell centres. Cells with the nodata sentinel (outside
the assessed area) are ignored; a footprint with no valid cell gets
``None`` for both attributes.

Polygon inputs are read as GeoJSON with the standard library (no
``fiona`` needed) and reprojected to the raster's CRS for sampling. The
output is written as GeoJSON preserving the original geometries,
coordinate reference system and properties, with the two BAL attributes
added.

The whole FeatureCollection is held in memory and rewritten, which is
ample for typical locality-scale inputs (thousands of polygons) but not
intended for state-wide layers of millions of features.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from bal_toolbox_qgis.balcore import _rio as rasterio
from bal_toolbox_qgis.balcore._rio.errors import WindowError
from bal_toolbox_qgis.balcore._rio.features import geometry_mask
from bal_toolbox_qgis.balcore._rio.warp import transform_geom

from bal_toolbox_qgis.balcore import tables
from bal_toolbox_qgis.balcore.raster import geojson_crs, set_geojson_crs

logger = logging.getLogger(__name__)


def _load_feature_collection(path: Path) -> dict[str, Any]:
    """Load a GeoJSON FeatureCollection from disk.

    Args:
        path: Path to a GeoJSON file.

    Returns:
        The parsed GeoJSON document.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the document is not a FeatureCollection.
    """
    if not path.exists():
        raise FileNotFoundError(f"Polygon file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        doc: dict[str, Any] = json.load(handle)
    if doc.get("type") != "FeatureCollection":
        raise ValueError(f"Polygon file must be a GeoJSON FeatureCollection: {path}")
    return doc


def _zonal_stats(
    src: rasterio.io.DatasetReader,
    geometry: dict[str, Any],
    nodata: float,
) -> tuple[float | None, float | None]:
    """Compute (max, modal) BAL over the cells a geometry touches.

    Args:
        src: An open BAL raster dataset.
        geometry: A geometry in the raster's CRS (GeoJSON mapping).
        nodata: The raster's nodata sentinel to exclude.

    Returns:
        A tuple ``(bal_max, bal_dominant)``; both ``None`` if the
        footprint covers no valid (non-nodata) cell. ``bal_dominant``
        breaks count ties toward the more severe (higher) band.
    """
    from bal_toolbox_qgis.balcore._rio.features import bounds as feature_bounds
    from bal_toolbox_qgis.balcore._rio.windows import Window, from_bounds

    try:
        minx, miny, maxx, maxy = feature_bounds(geometry)
        window = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
        window = window.round_offsets(op="floor").round_lengths(op="ceil")
    except (WindowError, ValueError, KeyError, IndexError, TypeError):
        # Degenerate or malformed geometry -> no zonal statistics.
        return None, None

    # Clamp to the raster, padding one cell on the far edges so a small
    # footprint still captures the cell(s) it sits on under all_touched.
    col_off = max(0, int(window.col_off))
    row_off = max(0, int(window.row_off))
    col_end = min(src.width, int(window.col_off) + int(window.width) + 1)
    row_end = min(src.height, int(window.row_off) + int(window.height) + 1)
    if col_end <= col_off or row_end <= row_off:
        return None, None

    read_window = Window(col_off, row_off, col_end - col_off, row_end - row_off)
    data = src.read(1, window=read_window)
    win_transform = src.window_transform(read_window)

    inside = geometry_mask(
        [geometry],
        out_shape=data.shape,
        transform=win_transform,
        invert=True,
        all_touched=True,
    )
    values = data[inside & (data != nodata)]
    if values.size == 0:
        return None, None

    bal_max = float(np.max(values))
    counts = Counter(float(v) for v in values)
    top = max(counts.values())
    # Conservative tie-break: among the most-common bands, take the worst.
    modal = max(value for value, count in counts.items() if count == top)
    return bal_max, modal


def assign_bal_to_polygons(
    bal_raster: Path,
    polygons: Path,
    output: Path,
    overwrite: bool = False,
    output_crs: str | None = None,
) -> int:
    """Assign ``bal_max`` and ``bal_dominant`` to each polygon footprint.

    Args:
        bal_raster: Path to a BAL GeoTIFF (e.g. ``bal_max.tif``).
        polygons: Path to a GeoJSON FeatureCollection of polygons (e.g.
            building outlines or cadastral parcels).
        output: Path to write the annotated GeoJSON to.
        overwrite: Replace ``output`` if it exists; otherwise raise.
        output_crs: Coordinate reference system for the written GeoJSON
            (e.g. ``"EPSG:4283"``). ``None`` (the default) keeps each
            footprint in its input CRS. Sampling always reprojects to the
            BAL raster's CRS regardless.

    Returns:
        The number of features written.

    Raises:
        FileNotFoundError: If an input file is missing.
        FileExistsError: If ``output`` exists and ``overwrite`` is False.
        ValueError: If inputs are malformed.
    """
    if not bal_raster.exists():
        raise FileNotFoundError(f"BAL raster not found: {bal_raster}")
    if output.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output}. Re-run with overwrite enabled "
            "(--overwrite) to replace it."
        )

    doc = _load_feature_collection(polygons)
    src_crs = geojson_crs(doc)
    features = doc.get("features", [])
    logger.info("Assigning BAL to %d footprint(s) from %s", len(features), polygons)

    reproject_out = output_crs is not None and output_crs != src_crs
    with rasterio.open(bal_raster) as src:
        raster_crs = src.crs
        nodata = src.nodata if src.nodata is not None else float(tables.NODATA)
        rated = 0
        for feature in features:
            geometry = feature.get("geometry")
            props = feature.setdefault("properties", {})
            if geometry is None:
                props["bal_max"] = None
                props["bal_dominant"] = None
                continue
            # Reproject the footprint into the raster CRS for sampling.
            geom_in_raster = (
                transform_geom(src_crs, raster_crs.to_string(), geometry)
                if raster_crs is not None
                else geometry
            )
            bal_max, modal = _zonal_stats(src, geom_in_raster, float(nodata))
            props["bal_max"] = bal_max
            props["bal_dominant"] = modal
            if bal_max is not None:
                rated += 1
            if reproject_out:
                feature["geometry"] = transform_geom(src_crs, output_crs, geometry)

    if reproject_out:
        assert output_crs is not None  # reproject_out implies it is set
        set_geojson_crs(doc, output_crs)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(doc, handle)
        handle.write("\n")
    logger.info(
        "Wrote %s (%d of %d footprints rated; rest fell outside the BAL grid)",
        output,
        rated,
        len(features),
    )
    return len(features)
