"""Raster input/output helpers built on :mod:`rasterio`.

These functions replace the ``arcpy`` raster handling of the original
Geoscience Australia toolbox. Rasters are read into :mod:`numpy` arrays
with a consistent nodata sentinel and written back as GeoTIFFs that
preserve the source coordinate reference system and geotransform. A
reprojection helper aligns mismatched inputs onto a common grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from bal_toolbox_qgis.balcore import _rio as rasterio
from numpy.typing import NDArray
from bal_toolbox_qgis.balcore._rio.crs import CRS
from bal_toolbox_qgis.balcore._rio.enums import Resampling
from bal_toolbox_qgis.balcore._rio.transform import Affine, array_bounds
from bal_toolbox_qgis.balcore._rio.warp import reproject
from bal_toolbox_qgis.balcore._rio.windows import Window, from_bounds
from bal_toolbox_qgis.balcore._rio.windows import transform as window_transform_fn

from bal_toolbox_qgis.balcore import tables

#: Maximum band size (in pixels) that :func:`read_raster` will load whole.
#: Rasters larger than this must be read through an area-of-interest window
#: (see :func:`read_raster_window`) to avoid exhausting memory on large,
#: e.g. national, rasters. Roughly 250M px (~2 GB as float64).
_MAX_FULL_READ_PIXELS = 250_000_000


def _apply_source_nodata(data: NDArray[np.float64], nodata: float | None) -> None:
    """Replace a source raster's nodata value with :data:`tables.NODATA`.

    Handles a NaN nodata declaration explicitly: ``data == np.nan`` is
    ``False`` everywhere, so a plain equality test would leave NaN cells
    (a common float-DEM convention) untouched and let them flow into the
    calculation as if valid. Mutates ``data`` in place.

    Args:
        data: Band data as float64 (modified in place).
        nodata: The source raster's declared nodata value, or ``None``.
    """
    if nodata is None:
        return
    if np.isnan(nodata):
        data[np.isnan(data)] = tables.NODATA
    else:
        data[data == nodata] = tables.NODATA


@dataclass(frozen=True, slots=True)
class RasterGrid:
    """Geospatial grid definition shared by a set of aligned rasters.

    Attributes:
        transform: Affine geotransform mapping pixel to world coordinates.
        crs: Coordinate reference system, or ``None`` if undefined.
        pixel_width: Cell width in projection units (metres).
        pixel_height: Cell height in projection units (metres).
        shape: Grid extent as ``(rows, cols)``.
    """

    transform: Affine
    crs: CRS | None
    pixel_width: float
    pixel_height: float
    shape: tuple[int, int]


def read_raster(path: Path) -> tuple[NDArray[np.float64], RasterGrid]:
    """Read the first band of a raster into a float array.

    Args:
        path: Path to a raster readable by GDAL/rasterio.

    Returns:
        A tuple of the band data (with the source nodata replaced by
        :data:`tables.NODATA`) and its :class:`RasterGrid`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Raster not found: {path}")

    with rasterio.open(path) as src:
        band_size = int(src.width) * int(src.height)
        if band_size > _MAX_FULL_READ_PIXELS:
            raise ValueError(
                f"Raster {path.name} has {band_size:,} pixels "
                f"({src.width} x {src.height}), exceeding the whole-raster "
                f"read limit of {_MAX_FULL_READ_PIXELS:,} pixels. Supply an "
                "area of interest (AOI/window) to read only the region you "
                "need; see read_raster_window for large national rasters."
            )
        data = src.read(1).astype(np.float64)
        _apply_source_nodata(data, src.nodata)
        grid = RasterGrid(
            transform=src.transform,
            crs=src.crs,
            pixel_width=abs(src.transform.a),
            pixel_height=abs(src.transform.e),
            shape=(src.height, src.width),
        )
    return data, grid


def matches_grid(grid: RasterGrid, reference: RasterGrid) -> bool:
    """Return whether two grids are aligned closely enough to skip warping.

    Grids match when they share the same shape, cell size and coordinate
    reference system, and their geotransform origins coincide.

    Args:
        grid: Grid to test.
        reference: Reference grid to compare against.

    Returns:
        ``True`` if ``grid`` can be used directly on ``reference``'s grid.
    """
    return (
        grid.shape == reference.shape
        and bool(np.isclose(grid.pixel_width, reference.pixel_width))
        and bool(np.isclose(grid.pixel_height, reference.pixel_height))
        and grid.crs == reference.crs
        and bool(
            np.allclose(
                tuple(grid.transform)[:6],
                tuple(reference.transform)[:6],
                rtol=0,
                atol=1e-6,
            )
        )
    )


def reproject_to_grid(
    data: NDArray[np.float64],
    source: RasterGrid,
    reference: RasterGrid,
    resampling: Resampling = Resampling.nearest,
) -> NDArray[np.float64]:
    """Resample/reproject a raster onto a reference grid.

    The source array is warped so that it shares the reference grid's
    coordinate reference system, cell size, extent and shape. Cells with
    no source coverage are filled with :data:`tables.NODATA`.

    Args:
        data: Source raster data, using :data:`tables.NODATA` for nodata.
        source: Grid describing ``data``.
        reference: Target grid to align to.
        resampling: Resampling method. Defaults to nearest neighbour,
            which is correct for categorical vegetation classes.

    Returns:
        The reprojected array on the reference grid.

    Raises:
        ValueError: If either grid is missing a coordinate reference
            system, which is required to warp between them.
    """
    if source.crs is None or reference.crs is None:
        raise ValueError(
            "Both rasters need a coordinate reference system to be "
            "reprojected onto a common grid; one of them is undefined."
        )

    destination = np.full(reference.shape, float(tables.NODATA), dtype=np.float64)
    reproject(
        source=data,
        destination=destination,
        src_transform=source.transform,
        src_crs=source.crs,
        src_nodata=float(tables.NODATA),
        dst_transform=reference.transform,
        dst_crs=reference.crs,
        dst_nodata=float(tables.NODATA),
        resampling=resampling,
    )
    return destination


def _remove_raster_and_sidecars(path: Path) -> None:
    """Delete a raster and any GDAL sidecar files that accompany it.

    Removes ``path`` itself plus the common GDAL sidecars. GDAL names
    external overviews by appending to the full filename (``foo.tif.ovr``,
    ``foo.tif.aux.xml``), not by swapping the suffix (``foo.ovr``), so the
    append form is what must be deleted; the suffix-swapped forms are kept
    as a defensive fallback for tools that use them. Missing sidecars are
    ignored.

    Args:
        path: The raster path being replaced.
    """
    candidates = [
        path,
        path.with_name(path.name + ".aux.xml"),
        path.with_name(path.name + ".ovr"),
        path.with_name(path.name + ".rrd"),
        path.with_suffix(".ovr"),
        path.with_suffix(".rrd"),
    ]
    for candidate in candidates:
        candidate.unlink(missing_ok=True)


def write_raster(
    path: Path,
    data: NDArray[np.floating] | NDArray[np.integer],
    grid: RasterGrid,
    overwrite: bool = False,
    dtype: np.dtype[Any] | type | str | None = None,
) -> None:
    """Write a single-band raster, preserving georeferencing.

    Args:
        path: Destination GeoTIFF path; parent directories are created.
        data: Two-dimensional array to write.
        grid: Geospatial grid describing ``data``.
        overwrite: If the destination already exists, replace it when
            ``True``; raise :class:`FileExistsError` when ``False``. On
            overwrite, known GDAL sidecar files (``.aux.xml``, ``.ovr``,
            ``.rrd``) are also removed so stale statistics or overviews do
            not describe the new raster.
        dtype: Optional output data type (e.g. ``numpy.uint8``). When
            ``None`` (the default) the array is written as ``float32``,
            preserving existing behaviour. When provided, the array is
            written with that type so masks/class rasters can be stored
            compactly; the nodata value is cast to match.

    Raises:
        FileExistsError: If ``path`` exists and ``overwrite`` is ``False``.
    """
    if path.exists():
        if not overwrite:
            raise FileExistsError(
                "Output raster already exists: "
                f"{path}. Re-run with overwrite enabled (--overwrite) to "
                "replace it."
            )
        # Remove the raster and any GDAL sidecars first, so the write
        # succeeds even where the driver cannot clobber in place and no
        # stale sidecar (statistics/overviews) is left describing old data.
        _remove_raster_and_sidecars(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.asarray(data)
    out_dtype = np.dtype(np.float32 if dtype is None else dtype)
    nodata_value: float | int = (
        float(tables.NODATA)
        if np.issubdtype(out_dtype, np.floating)
        else int(tables.NODATA)
    )
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": out_dtype,
        "nodata": nodata_value,
        "transform": grid.transform,
        "compress": "deflate",
    }
    if grid.crs is not None:
        profile["crs"] = grid.crs

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(out_dtype), 1)


def _transform_bounds(
    bounds: tuple[float, float, float, float],
    src_crs: CRS | str | None,
    dst_crs: CRS | None,
) -> tuple[float, float, float, float]:
    """Transform a bounding box into a target coordinate reference system.

    Args:
        bounds: Bounding box ``(xmin, ymin, xmax, ymax)``.
        src_crs: Coordinate reference system of ``bounds``; if ``None``
            the bounds are assumed already in ``dst_crs``.
        dst_crs: Target coordinate reference system.

    Returns:
        The bounding box in ``dst_crs`` coordinates.
    """
    if src_crs is None or dst_crs is None:
        return bounds
    source = CRS.from_user_input(src_crs)
    if source == dst_crs:
        return bounds
    from bal_toolbox_qgis.balcore._rio.warp import transform_bounds

    xmin, ymin, xmax, ymax = transform_bounds(source, dst_crs, *bounds)
    return (xmin, ymin, xmax, ymax)


def _cover_window(window: Window) -> Window:
    """Round a fractional window outward to the whole pixels it touches.

    ``Window.round_offsets("floor").round_lengths("ceil")`` is wrong: it
    floors the offset but ceils the *original* fractional length, so
    ``floor(off) + ceil(len)`` can be one pixel short of ``ceil(off + len)``
    and the far (east/south) edge of the area of interest is dropped. This
    computes the stop from the original fractional extent first, guaranteeing
    the returned integer window is a superset of ``window``.

    Args:
        window: A fractional :class:`rasterio.windows.Window` from
            :func:`rasterio.windows.from_bounds`.

    Returns:
        An integer-aligned covering window.
    """
    col_off = math.floor(window.col_off)
    row_off = math.floor(window.row_off)
    col_stop = math.ceil(window.col_off + window.width)
    row_stop = math.ceil(window.row_off + window.height)
    return Window(col_off, row_off, col_stop - col_off, row_stop - row_off)


def _clamp_window(
    window: Window,
    width: int,
    height: int,
) -> Window | None:
    """Clamp a pixel window to a raster extent, or ``None`` if disjoint.

    Args:
        window: A :class:`rasterio.windows.Window` with integer offsets
            and lengths.
        width: Raster width in pixels.
        height: Raster height in pixels.

    Returns:
        The clamped :class:`rasterio.windows.Window`, or ``None`` if the
        window lies entirely outside the raster.
    """
    col_off = int(window.col_off)
    row_off = int(window.row_off)
    col_end = col_off + int(window.width)
    row_end = row_off + int(window.height)

    col_off_c = max(0, col_off)
    row_off_c = max(0, row_off)
    col_end_c = min(width, col_end)
    row_end_c = min(height, row_end)

    if col_end_c <= col_off_c or row_end_c <= row_off_c:
        return None
    return Window(col_off_c, row_off_c, col_end_c - col_off_c, row_end_c - row_off_c)


def footprint_window(
    geometry: dict[str, object],
    grid_transform: Affine,
    width: int,
    height: int,
) -> Window | None:
    """Return the clamped pixel window covering a footprint geometry.

    Computes the pixel window a footprint geometry occupies on a grid,
    padding one cell on the far (east/south) edges so a small footprint
    still captures the cell(s) it sits on under ``all_touched`` sampling.
    Shared by :mod:`bal_toolbox_qgis.balcore.zonal` and
    :mod:`bal_toolbox_qgis.balcore.history_overlay`.

    Args:
        geometry: A geometry in the grid's CRS (GeoJSON mapping).
        grid_transform: The grid's affine transform.
        width: Grid width in pixels.
        height: Grid height in pixels.

    Returns:
        A :class:`rasterio.windows.Window`, or ``None`` if the footprint
        is degenerate/malformed or lies entirely outside the grid.
    """
    from bal_toolbox_qgis.balcore._rio.errors import WindowError
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


def read_raster_window(
    path: Path,
    bounds: tuple[float, float, float, float],
    bounds_crs: CRS | str | None,
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Read only the window of a raster covering an area of interest.

    This avoids loading an entire large (e.g. continental) raster into
    memory: the requested ``bounds`` are transformed into the raster's
    own coordinate reference system and only the covering pixel window is
    read.

    Args:
        path: Path to a raster readable by GDAL/rasterio.
        bounds: Area-of-interest bounds ``(xmin, ymin, xmax, ymax)``.
        bounds_crs: Coordinate reference system of ``bounds``; if
            ``None`` the bounds are assumed to be in the raster's CRS.

    Returns:
        A tuple of the windowed band data (source nodata replaced by
        :data:`tables.NODATA`) and its :class:`RasterGrid`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the area of interest does not overlap the raster.
    """
    if not path.exists():
        raise FileNotFoundError(f"Raster not found: {path}")

    with rasterio.open(path) as src:
        raster_bounds = _transform_bounds(bounds, bounds_crs, src.crs)
        window = from_bounds(*raster_bounds, transform=src.transform)
        # Round outward to whole pixels and clamp to the raster extent.
        window = _cover_window(window)
        clamped = _clamp_window(window, src.width, src.height)
        if clamped is None:
            raise ValueError(
                "Area of interest does not overlap the raster "
                f"{path.name}; check the AOI bounds and CRS."
            )
        window = clamped
        data = src.read(1, window=window).astype(np.float64)
        _apply_source_nodata(data, src.nodata)
        window_transform = src.window_transform(window)
        grid = RasterGrid(
            transform=window_transform,
            crs=src.crs,
            pixel_width=abs(src.transform.a),
            pixel_height=abs(src.transform.e),
            shape=(int(window.height), int(window.width)),
        )
    return data, grid


def crop_to_bounds(
    data: NDArray[np.float64],
    grid: RasterGrid,
    bounds: tuple[float, float, float, float],
    bounds_crs: CRS | str | None,
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Crop an in-memory raster to an area of interest.

    Args:
        data: Raster data already loaded into memory.
        grid: Grid describing ``data``.
        bounds: Area-of-interest bounds ``(xmin, ymin, xmax, ymax)``.
        bounds_crs: Coordinate reference system of ``bounds``; if
            ``None`` the bounds are assumed to be in ``grid``'s CRS.

    Returns:
        The cropped data and its :class:`RasterGrid`.

    Raises:
        ValueError: If the area of interest does not overlap ``grid``.
    """
    grid_bounds = _transform_bounds(bounds, bounds_crs, grid.crs)
    window = from_bounds(*grid_bounds, transform=grid.transform)
    window = _cover_window(window)
    clamped = _clamp_window(window, grid.shape[1], grid.shape[0])
    if clamped is None:
        raise ValueError("Area of interest does not overlap the DEM.")
    window = clamped

    row_off, col_off = int(window.row_off), int(window.col_off)
    rows, cols = int(window.height), int(window.width)
    cropped = data[row_off : row_off + rows, col_off : col_off + cols].copy()
    new_transform = window_transform_fn(window, grid.transform)
    new_grid = RasterGrid(
        transform=new_transform,
        crs=grid.crs,
        pixel_width=grid.pixel_width,
        pixel_height=grid.pixel_height,
        shape=(rows, cols),
    )
    return cropped, new_grid


#: File suffixes read natively as GeoJSON (no :mod:`fiona` needed).
_GEOJSON_SUFFIXES: frozenset[str] = frozenset({".geojson", ".json"})


def _is_geojson(path: Path) -> bool:
    """Return whether a path should be read as GeoJSON (by suffix)."""
    return path.suffix.lower() in _GEOJSON_SUFFIXES


def geojson_crs(doc: dict[str, object]) -> str:
    """Return a GeoJSON document's CRS as an ``"EPSG:NNNN"`` string.

    Reads the (non-standard but common) ``crs`` name member, e.g.
    ``urn:ogc:def:crs:EPSG::4283`` -> ``"EPSG:4283"``. The OGC ``CRS84``
    family (e.g. ``urn:ogc:def:crs:OGC:1.3:CRS84``) is the RFC 7946
    default lon/lat WGS84 and maps to ``"EPSG:4326"``. Per RFC 7946,
    GeoJSON with no ``crs`` member is also WGS84, so ``"EPSG:4326"`` is
    the default.

    Args:
        doc: A parsed GeoJSON mapping.

    Returns:
        The CRS string (defaulting to ``"EPSG:4326"``).
    """
    crs_member = doc.get("crs")
    if isinstance(crs_member, dict):
        properties = crs_member.get("properties", {})
        name = properties.get("name", "") if isinstance(properties, dict) else ""
        token = str(name).rsplit(":", 1)[-1].strip().upper()
        # CRS84/CRS83/CRS27 are OGC lon/lat names, not EPSG codes; the
        # trailing digits ("84") are not an EPSG code, so map to WGS84.
        if token.startswith("CRS"):
            return "EPSG:4326"
        digits = "".join(ch for ch in token if ch.isdigit())
        if digits:
            return f"EPSG:{digits}"
    return "EPSG:4326"


def set_geojson_crs(doc: dict[str, object], crs: str) -> None:
    """Set a GeoJSON document's named ``crs`` member in place.

    Writes the OGC URN form, e.g. ``"EPSG:4283"`` ->
    ``urn:ogc:def:crs:EPSG::4283``. A WGS84 ``crs`` is left implicit
    (the member is removed) per RFC 7946, which states GeoJSON is WGS84
    by default.

    Args:
        doc: A parsed GeoJSON mapping (mutated in place).
        crs: The CRS string to record (e.g. ``"EPSG:4283"``).
    """
    token = crs.strip().upper()
    if token in {"EPSG:4326", "OGC:CRS84"}:
        doc.pop("crs", None)
        return
    code = "".join(ch for ch in token.rsplit(":", 1)[-1] if ch.isdigit())
    name = f"urn:ogc:def:crs:EPSG::{code}" if code else crs
    doc["crs"] = {"type": "name", "properties": {"name": name}}


def _geojson_geometries(
    path: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """Read geometries and CRS from a GeoJSON file using the stdlib.

    GeoJSON is plain JSON, so this needs no GDAL/``fiona``. Handles a
    ``FeatureCollection``, a single ``Feature``, or a bare geometry, and
    extracts an EPSG code from the (non-standard but common) ``crs``
    member. Per RFC 7946, GeoJSON with no ``crs`` member is WGS84
    (EPSG:4326).

    Args:
        path: Path to a ``.geojson``/``.json`` file.

    Returns:
        A tuple of the list of GeoJSON geometry mappings and a CRS string
        (e.g. ``"EPSG:4283"``), defaulting to ``"EPSG:4326"``.

    Raises:
        ValueError: If the file has no usable geometry.
    """
    import json

    with path.open("r", encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict):
        raise ValueError(f"GeoJSON root must be an object: {path}")

    obj_type = doc.get("type")
    if obj_type == "FeatureCollection":
        features = doc.get("features", [])
        geometries = [
            f["geometry"]
            for f in features
            if isinstance(f, dict) and f.get("geometry") is not None
        ]
    elif obj_type == "Feature":
        geometry = doc.get("geometry")
        geometries = [geometry] if geometry is not None else []
    elif obj_type in {"Polygon", "MultiPolygon", "GeometryCollection"}:
        geometries = [doc]
    else:
        raise ValueError(f"Unsupported GeoJSON type {obj_type!r} in {path}")

    if not geometries:
        raise ValueError(f"GeoJSON AOI file has no geometry: {path}")

    return geometries, geojson_crs(doc)


def _geometry_bounds(
    geometries: list[dict[str, object]],
) -> tuple[float, float, float, float]:
    """Compute the ``(xmin, ymin, xmax, ymax)`` bounds of GeoJSON geometries."""
    xs: list[float] = []
    ys: list[float] = []

    def _walk(coords: object) -> None:
        if (
            isinstance(coords, (list, tuple))
            and len(coords) >= 2
            and all(isinstance(v, (int, float)) for v in coords[:2])
        ):
            xs.append(float(coords[0]))
            ys.append(float(coords[1]))
        elif isinstance(coords, (list, tuple)):
            for item in coords:
                _walk(item)

    for geom in geometries:
        _walk(geom.get("coordinates", []))
    if not xs:
        raise ValueError("GeoJSON geometry has no coordinates.")
    return (min(xs), min(ys), max(xs), max(ys))


def read_polygon_bounds(
    path: Path,
) -> tuple[tuple[float, float, float, float], str | None]:
    """Read the bounding box and CRS of a polygon vector file.

    GeoJSON (``.geojson``/``.json``) is read natively with the standard
    library, so no extra dependency is needed. Other vector formats
    (shapefile, GeoPackage, ...) are read with the optional :mod:`fiona`
    dependency.

    Args:
        path: Path to a vector file.

    Returns:
        A tuple of the bounds ``(xmin, ymin, xmax, ymax)`` and the CRS as
        a string (e.g. ``"EPSG:4283"``), or ``None`` if undeclared.

    Raises:
        ImportError: If a non-GeoJSON file is given and :mod:`fiona` is
            not installed.
        ValueError: If the file contains no usable geometry.
    """
    if _is_geojson(path):
        geometries, crs = _geojson_geometries(path)
        return _geometry_bounds(geometries), crs

    try:
        from bal_toolbox_qgis.balcore._rio import _fiona as fiona
    except ImportError as err:  # pragma: no cover - exercised via message
        raise ImportError(
            "Reading a non-GeoJSON polygon AOI requires GDAL's Python bindings "
            "(osgeo), which ship with QGIS; or supply the AOI as GeoJSON "
            "(.geojson)."
        ) from err

    with fiona.open(path) as layer:
        bounds = layer.bounds  # (xmin, ymin, xmax, ymax)
        crs = layer.crs.to_string() if layer.crs else None
    if bounds is None:
        raise ValueError(f"Polygon AOI file has no features: {path}")
    return (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])), crs


def mask_to_polygon(
    data: NDArray[np.floating],
    grid: RasterGrid,
    path: Path,
) -> NDArray[np.float64]:
    """Set raster cells outside a polygon boundary to nodata.

    Args:
        data: Raster data aligned to ``grid``.
        grid: Grid describing ``data``.
        path: Path to a polygon vector file (shapefile, GeoJSON, ...).

    Returns:
        A copy of ``data`` with cells whose centre lies outside the
        polygon set to :data:`tables.NODATA`.

    Raises:
        ImportError: If a non-GeoJSON file is given and :mod:`fiona` is
            not installed.
    """
    from bal_toolbox_qgis.balcore._rio.features import geometry_mask
    from bal_toolbox_qgis.balcore._rio.warp import transform_geom

    if _is_geojson(path):
        geometries, layer_crs = _geojson_geometries(path)
    else:
        try:
            from bal_toolbox_qgis.balcore._rio import _fiona as fiona
        except ImportError as err:  # pragma: no cover - exercised via message
            raise ImportError(
                "Masking to a non-GeoJSON polygon AOI requires GDAL's Python "
                "bindings (osgeo), which ship with QGIS; or supply the AOI as "
                "GeoJSON (.geojson)."
            ) from err
        with fiona.open(path) as layer:
            layer_crs = layer.crs.to_string() if layer.crs else None
            geometries = [feature["geometry"] for feature in layer]

    if layer_crs is not None and grid.crs is not None:
        source = CRS.from_user_input(layer_crs)
        if source != grid.crs:
            geometries = [transform_geom(source, grid.crs, geom) for geom in geometries]

    outside = geometry_mask(
        geometries,
        out_shape=grid.shape,
        transform=grid.transform,
        invert=False,
    )
    result = np.asarray(data, dtype=np.float64).copy()
    result[outside] = float(tables.NODATA)
    return result


def reproject_raster(
    data: NDArray[np.floating] | NDArray[np.integer],
    grid: RasterGrid,
    dst_crs: CRS | str,
    resampling: Resampling = Resampling.nearest,
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Reproject a raster onto a new coordinate reference system.

    The destination grid, transform and shape are chosen automatically by
    :func:`rasterio.warp.calculate_default_transform` to cover the source
    extent at a comparable resolution. Cells with no source coverage are
    filled with :data:`tables.NODATA`.

    Categorical rasters (e.g. BAL bands or vegetation classes) must use
    the default nearest-neighbour resampling so no intermediate values are
    invented; continuous rasters (e.g. a DEM) should pass
    ``Resampling.bilinear``.

    Args:
        data: Source raster data, using :data:`tables.NODATA` for nodata.
        grid: Grid describing ``data`` (its ``crs`` must be defined).
        dst_crs: Target coordinate reference system (a
            :class:`rasterio.crs.CRS` or a string such as ``"EPSG:4283"``).
        resampling: Resampling method; defaults to nearest neighbour.

    Returns:
        The reprojected array and its :class:`RasterGrid`. When the source
        is already in ``dst_crs`` the input is returned unchanged.

    Raises:
        ValueError: If ``grid`` has no coordinate reference system, which
            is required to reproject from it.
    """
    from bal_toolbox_qgis.balcore._rio.warp import calculate_default_transform

    if grid.crs is None:
        raise ValueError(
            "Raster has no coordinate reference system; cannot reproject it "
            f"to {dst_crs}."
        )
    target = CRS.from_user_input(dst_crs)
    if target == grid.crs:
        return np.asarray(data, dtype=np.float64), grid

    left, bottom, right, top = array_bounds(
        grid.shape[0], grid.shape[1], grid.transform
    )
    dst_transform, dst_width, dst_height = calculate_default_transform(
        grid.crs,
        target,
        grid.shape[1],
        grid.shape[0],
        left=left,
        bottom=bottom,
        right=right,
        top=top,
    )
    destination = np.full(
        (dst_height, dst_width), float(tables.NODATA), dtype=np.float64
    )
    reproject(
        source=np.asarray(data, dtype=np.float64),
        destination=destination,
        src_transform=grid.transform,
        src_crs=grid.crs,
        src_nodata=float(tables.NODATA),
        dst_transform=dst_transform,
        dst_crs=target,
        dst_nodata=float(tables.NODATA),
        resampling=resampling,
    )
    dst_grid = RasterGrid(
        transform=dst_transform,
        crs=target,
        pixel_width=abs(dst_transform.a),
        pixel_height=abs(dst_transform.e),
        shape=(int(dst_height), int(dst_width)),
    )
    return destination, dst_grid
