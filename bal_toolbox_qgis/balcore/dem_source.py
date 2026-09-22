"""National DEM source: fetch a SRTM 1-Second window from the GA WCS.

The Australian national 1-second (~30 m) Digital Elevation Model is
published by Geoscience Australia as an OGC Web Coverage Service (WCS).
This module fetches only the window covering an area of interest and
reprojects it into a projected (metres) coordinate reference system, so
the BAL engine's slope/aspect calculation has the projected DEM it
requires. The continental coverage is never downloaded in full.

Network note
------------
``fetch_dem_window`` performs a live HTTP request to the GA service: a
WCS 1.0.0 ``GetCoverage`` request (built in
:mod:`bal_toolbox_qgis.balcore._wcs_request`) is downloaded to a temporary
GeoTIFF with :mod:`urllib`, then read back through the GDAL shim. GDAL's own
WCS *driver* is deliberately not used: the GA ArcGIS server rejects the
GetCoverage request GDAL builds for it (HTTP 400 "Malformed Result"), whereas
a plain WCS 1.0.0 KVP GetCoverage succeeds. The request is isolated in this
one function so the rest of the toolbox -- and the test suite -- can run
without network access (tests mock this function, or exercise the pure
request maths in ``_wcs_request`` directly).

Service (verified): the GA "Digital Elevation Model (DEM) of Australia
with 1 Second Grid" service, derived from the SRTM 1 Second product with
vegetation removed. It is served in EPSG:4326 (geographic degrees) and
the WCS 1.0.0 binding exposes it as coverage ``1``. Licence: CC BY 4.0,
(C) Commonwealth of Australia (Geoscience Australia) 2024.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

from bal_toolbox_qgis.balcore._wcs_request import (
    SRTM_CELL_SIZE_DEG,
    align_request_window,
    build_getcoverage_url,
    error_detail,
    looks_like_tiff,
)
from bal_toolbox_qgis.balcore.raster import RasterGrid

logger = logging.getLogger(__name__)

#: Default GA SRTM 1-Second DEM WCS endpoint and coverage (WCS 1.0.0).
GA_SRTM_WCS_URL: Final[str] = (
    "https://services.ga.gov.au/gis/services/DEM_SRTM_1Second_2024/MapServer/WCSServer?"
)
GA_SRTM_WCS_COVERAGE: Final[str] = "1"
GA_SRTM_WCS_VERSION: Final[str] = "1.0.0"

# ``SRTM_CELL_SIZE_DEG`` is imported from ``_wcs_request`` above and remains
# available as ``dem_source.SRTM_CELL_SIZE_DEG`` for backwards compatibility.

#: Names recognised in the YAML ``dem`` field for the national WCS source.
NATIONAL_DEM_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"srtm_1s", "srtm1s", "national", "ga_srtm"}
)


def is_national_dem(value: str) -> bool:
    """Return whether a ``dem`` config value selects the national WCS DEM.

    Args:
        value: The raw ``dem`` value from the configuration.

    Returns:
        ``True`` if ``value`` (case-insensitive) names the national DEM
        source rather than a file path.
    """
    return value.strip().lower() in NATIONAL_DEM_KEYWORDS


def _mga_zone_number(longitude: float) -> int:
    """Return the (UTM/MGA) zone number for a longitude in degrees east.

    UTM/MGA zones are 6 degrees of longitude wide; zone 1 starts at 180W,
    so zone 49 starts at 108E.

    Args:
        longitude: Longitude in degrees east.

    Returns:
        The 1-based UTM/MGA zone number.
    """
    return int((longitude + 180.0) // 6.0) + 1


def mga2020_epsg_for_longitude(longitude: float) -> int:
    """Return the GDA2020 MGA zone EPSG code for an Australian longitude.

    Map Grid of Australia (MGA) zones are UTM zones; the GDA2020 variants
    are EPSG:7849 (zone 49) through EPSG:7856 (zone 56), which together
    span the Australian mainland and territories. The zone is selected
    from the longitude alone (see :func:`_mga_zone_number`).

    Args:
        longitude: Longitude in degrees east (e.g. the AOI centre).

    Returns:
        The EPSG code of the covering GDA2020 MGA zone.

    Raises:
        ValueError: If the longitude is outside the MGA zone 49-56 range
            (about 108E-156E) that the codes cover.
    """
    zone = _mga_zone_number(longitude)
    if not 49 <= zone <= 56:
        raise ValueError(
            f"Longitude {longitude} is outside MGA zones 49-56 "
            "(about 108E-156E); specify an Australian area of interest."
        )
    return 7800 + zone


def aoi_centre_longitude(
    bounds: tuple[float, float, float, float],
    bounds_crs: str | None,
) -> float:
    """Return the AOI centre longitude in degrees east.

    Args:
        bounds: AOI bounds ``(xmin, ymin, xmax, ymax)``.
        bounds_crs: CRS of ``bounds`` (e.g. ``"EPSG:4326"`` or a
            projected CRS), or ``None`` to assume geographic degrees
            (EPSG:4326).

    Returns:
        The centre longitude in degrees east.
    """
    centre_x = 0.5 * (bounds[0] + bounds[2])
    centre_y = 0.5 * (bounds[1] + bounds[3])
    if bounds_crs is None:
        return centre_x
    from bal_toolbox_qgis.balcore._rio.crs import CRS
    from bal_toolbox_qgis.balcore._rio.warp import transform

    src = CRS.from_user_input(bounds_crs)
    geographic = CRS.from_epsg(4326)
    if src == geographic:
        return centre_x
    xs, _ = transform(src, geographic, [centre_x], [centre_y])
    return float(xs[0])


def mga_epsg_for_aoi(
    bounds: tuple[float, float, float, float],
    bounds_crs: str | None,
) -> int:
    """Pick the GDA2020 MGA zone EPSG for an AOI, warning if it spans zones.

    The DEM is reprojected into a single MGA (UTM) zone chosen from the
    AOI centre. MGA zones are 6 degrees of longitude wide; an AOI that
    spans more than one zone cannot be represented without distortion
    away from the chosen zone's central meridian. This is fine for the
    locality-scale areas the toolbox targets, but a very wide AOI is
    flagged so the result is not silently distorted.

    Args:
        bounds: AOI bounds ``(xmin, ymin, xmax, ymax)``.
        bounds_crs: CRS of ``bounds`` (``None`` assumes EPSG:4326).

    Returns:
        The EPSG code of the MGA zone covering the AOI centre.

    Raises:
        ValueError: If the AOI centre is outside MGA zones 49-56.
    """
    from bal_toolbox_qgis.balcore._rio.crs import CRS
    from bal_toolbox_qgis.balcore._rio.warp import transform

    centre_lon = aoi_centre_longitude(bounds, bounds_crs)
    # Longitudes of the AOI's west and east edges, in degrees, to test span.
    if bounds_crs is None or CRS.from_user_input(bounds_crs) == CRS.from_epsg(4326):
        west_lon, east_lon = bounds[0], bounds[2]
    else:
        src = CRS.from_user_input(bounds_crs)
        ys = [0.5 * (bounds[1] + bounds[3])] * 2
        lons, _ = transform(src, CRS.from_epsg(4326), [bounds[0], bounds[2]], ys)
        west_lon, east_lon = float(lons[0]), float(lons[1])

    west_zone = _mga_zone_number(west_lon)
    east_zone = _mga_zone_number(east_lon)
    if west_zone != east_zone:
        logger.warning(
            "Area of interest spans MGA zones %d-%d; using the centre zone "
            "(%d) for the whole output. Results far from that zone's central "
            "meridian may be distorted -- split a wide AOI by zone if needed.",
            min(west_zone, east_zone),
            max(west_zone, east_zone),
            _mga_zone_number(centre_lon),
        )
    return mga2020_epsg_for_longitude(centre_lon)


def assert_geographic_bounds(bounds: tuple[float, float, float, float]) -> None:
    """Validate that bounds look like EPSG:4326 longitude/latitude degrees.

    The national DEM source interprets an AOI with no explicit ``crs`` as
    geographic degrees. This guards the common mistake of supplying
    projected (metre) coordinates without a ``crs``, which would otherwise
    be silently misread.

    Args:
        bounds: AOI bounds ``(xmin, ymin, xmax, ymax)`` assumed geographic.

    Raises:
        ValueError: If any coordinate is outside the valid lon/lat range.
    """
    xmin, ymin, xmax, ymax = bounds
    if not (-180.0 <= xmin <= 180.0 and -180.0 <= xmax <= 180.0):
        raise ValueError(
            "Area-of-interest longitudes "
            f"({xmin}, {xmax}) are outside -180..180; for the national DEM "
            "an AOI without 'crs' is assumed to be EPSG:4326 degrees. Set "
            "'aoi.crs' if the bounds are in a projected CRS."
        )
    if not (-90.0 <= ymin <= 90.0 and -90.0 <= ymax <= 90.0):
        raise ValueError(
            "Area-of-interest latitudes "
            f"({ymin}, {ymax}) are outside -90..90; for the national DEM "
            "an AOI without 'crs' is assumed to be EPSG:4326 degrees. Set "
            "'aoi.crs' if the bounds are in a projected CRS."
        )


def fetch_dem_window(
    bounds: tuple[float, float, float, float],
    bounds_crs: str | None,
    service_url: str = GA_SRTM_WCS_URL,
    coverage: str = GA_SRTM_WCS_COVERAGE,
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Fetch the SRTM 1-Second DEM window covering an AOI from the WCS.

    The returned DEM is in the service's native EPSG:4326 (geographic
    degrees); reprojection to a projected CRS is the caller's
    responsibility (see :func:`reproject_dem_to_mga`).

    Args:
        bounds: AOI bounds ``(xmin, ymin, xmax, ymax)``.
        bounds_crs: CRS of ``bounds``; if not EPSG:4326 the bounds are
            transformed to geographic degrees for the WCS request. ``None``
            assumes the bounds are already geographic.
        service_url: WCS endpoint (defaults to the GA SRTM service).
        coverage: WCS coverage identifier (defaults to ``"1"``).

    Returns:
        A tuple of the DEM window data (nodata as
        :data:`bal_toolbox_qgis.balcore.tables.NODATA`) and its :class:`RasterGrid`.

    Raises:
        ValueError: If the AOI does not intersect the DEM coverage.
        RuntimeError: If the WCS request fails.
    """
    from bal_toolbox_qgis.balcore._rio.crs import CRS
    from bal_toolbox_qgis.balcore._rio.warp import transform_bounds

    geographic = CRS.from_epsg(4326)
    if bounds_crs is not None and CRS.from_user_input(bounds_crs) != geographic:
        geo_bounds = transform_bounds(
            CRS.from_user_input(bounds_crs), geographic, *bounds
        )
    else:
        geo_bounds = bounds

    # Snap the AOI outward to whole SRTM cells and clamp to the coverage
    # extent, then request exactly that window as a GeoTIFF over plain HTTP.
    request_bounds, width, height = align_request_window(geo_bounds)
    url = build_getcoverage_url(
        service_url, coverage, GA_SRTM_WCS_VERSION, request_bounds, width, height
    )
    logger.info(
        "Requesting DEM window %s (%d x %d px) from GA SRTM WCS",
        request_bounds,
        width,
        height,
    )

    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        tif_path = Path(tmp_dir) / "dem_window.tif"
        _download_coverage(url, tif_path)
        return _read_geotiff_window(str(tif_path))


def _download_coverage(url: str, out_path: Path) -> None:
    """Download a WCS ``GetCoverage`` GeoTIFF to ``out_path``.

    Args:
        url: The fully-formed WCS 1.0.0 ``GetCoverage`` request URL.
        out_path: Destination file for the returned GeoTIFF bytes.

    Raises:
        RuntimeError: If the service is unreachable, returns an HTTP error, or
            responds with something other than a GeoTIFF (e.g. an XML/HTML
            ``ServiceExceptionReport``).
    """
    import ssl
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url, headers={"User-Agent": "bal_toolbox_qgis WCS client"}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=120, context=ssl.create_default_context()
        ) as response:
            payload: bytes = response.read()
    except urllib.error.HTTPError as err:  # pragma: no cover - network/service
        body = error_detail(err.read())
        message = f"National DEM WCS request failed (HTTP {err.code})."
        raise RuntimeError(f"{message} {body}".strip()) from err
    except (urllib.error.URLError, OSError) as err:  # pragma: no cover - network
        raise RuntimeError(
            f"Could not reach the national DEM WCS service: {err}"
        ) from err

    if not looks_like_tiff(payload):  # pragma: no cover - service error path
        raise RuntimeError(
            "The national DEM WCS did not return a GeoTIFF. "
            f"Service response: {error_detail(payload)}"
        )
    out_path.write_bytes(payload)


def _read_geotiff_window(
    path: str,
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Read a downloaded DEM-window GeoTIFF into ``(data, grid)``.

    Args:
        path: Path to a GeoTIFF written by :func:`_download_coverage`.

    Returns:
        ``(data, grid)`` for the window, in the file's native (geographic) CRS.

    Raises:
        RuntimeError: If the file cannot be read as a raster.
    """
    from bal_toolbox_qgis.balcore import _rio as rasterio
    from bal_toolbox_qgis.balcore._rio.errors import RasterioError

    from bal_toolbox_qgis.balcore.raster import _apply_source_nodata

    try:
        with rasterio.open(path) as src:
            data = src.read(1).astype(np.float64)
            _apply_source_nodata(data, src.nodata)
            grid = RasterGrid(
                transform=src.transform,
                crs=src.crs,
                pixel_width=abs(src.transform.a),
                pixel_height=abs(src.transform.e),
                shape=(int(src.height), int(src.width)),
            )
    except (RasterioError, OSError) as err:  # pragma: no cover - service/io
        raise RuntimeError(
            f"Failed to read the national DEM window returned by the WCS: {err}"
        ) from err
    return data, grid


def reproject_dem_to_mga(
    data: NDArray[np.float64],
    grid: RasterGrid,
    target_epsg: int,
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Reproject a geographic DEM window into a projected MGA grid.

    The DEM is warped from its geographic CRS into the target projected
    CRS at the equivalent SRTM cell size (~30 m), using bilinear
    resampling (appropriate for continuous elevation).

    Args:
        data: DEM window data in a geographic CRS.
        grid: Grid describing ``data``.
        target_epsg: EPSG code of the projected target CRS (a GDA2020
            MGA zone).

    Returns:
        The reprojected DEM and its projected :class:`RasterGrid`.
    """
    from bal_toolbox_qgis.balcore._rio.crs import CRS
    from bal_toolbox_qgis.balcore._rio.enums import Resampling
    from bal_toolbox_qgis.balcore._rio.warp import calculate_default_transform, reproject

    from bal_toolbox_qgis.balcore import tables

    dst_crs = CRS.from_epsg(target_epsg)
    left, bottom, right, top = _grid_bounds(grid)
    dst_transform, dst_width, dst_height = calculate_default_transform(
        grid.crs,
        dst_crs,
        grid.shape[1],
        grid.shape[0],
        left=left,
        bottom=bottom,
        right=right,
        top=top,
        resolution=30.0,
    )
    destination = np.full(
        (dst_height, dst_width), float(tables.NODATA), dtype=np.float64
    )
    reproject(
        source=data,
        destination=destination,
        src_transform=grid.transform,
        src_crs=grid.crs,
        src_nodata=float(tables.NODATA),
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=float(tables.NODATA),
        resampling=Resampling.bilinear,
    )
    dst_grid = RasterGrid(
        transform=dst_transform,
        crs=dst_crs,
        pixel_width=abs(dst_transform.a),
        pixel_height=abs(dst_transform.e),
        shape=(int(dst_height), int(dst_width)),
    )
    return destination, dst_grid


def _grid_bounds(grid: RasterGrid) -> tuple[float, float, float, float]:
    """Return the ``(left, bottom, right, top)`` world bounds of a grid.

    Args:
        grid: The raster grid.

    Returns:
        The grid's outer bounds in its own CRS units.
    """
    from bal_toolbox_qgis.balcore._rio.transform import array_bounds

    left, bottom, right, top = array_bounds(
        grid.shape[0], grid.shape[1], grid.transform
    )
    return (float(left), float(bottom), float(right), float(top))
