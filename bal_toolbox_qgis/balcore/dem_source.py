"""National DEM source: fetch a SRTM 1-Second window from the GA WCS.

The Australian national 1-second (~30 m) Digital Elevation Model is
published by Geoscience Australia as an OGC Web Coverage Service (WCS).
This module fetches only the window covering an area of interest and
reprojects it into a projected (metres) coordinate reference system, so
the BAL engine's slope/aspect calculation has the projected DEM it
requires. The continental coverage is never downloaded in full.

Network note
------------
``fetch_dem_window`` performs a live HTTP request to the GA service via
GDAL's WCS driver. The request is isolated in this one function so the
rest of the toolbox -- and the test suite -- can run without network
access (tests mock this function).

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

from bal_toolbox_qgis.balcore.raster import RasterGrid

logger = logging.getLogger(__name__)

#: Default GA SRTM 1-Second DEM WCS endpoint and coverage (WCS 1.0.0).
GA_SRTM_WCS_URL: Final[str] = (
    "https://services.ga.gov.au/gis/services/DEM_SRTM_1Second_2024/MapServer/WCSServer?"
)
GA_SRTM_WCS_COVERAGE: Final[str] = "1"
GA_SRTM_WCS_VERSION: Final[str] = "1.0.0"

#: Native cell size of the SRTM 1-second product in degrees (~30 m).
SRTM_CELL_SIZE_DEG: Final[float] = 1.0 / 3600.0

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

    # Escape XML metacharacters (<, >, &) in the interpolated values so the
    # descriptor is always well-formed, even if a service URL or coverage
    # identifier ever contains one of them.
    from xml.sax.saxutils import escape

    wcs_xml = (
        "<WCS_GDAL>"
        f"<ServiceURL>{escape(service_url)}</ServiceURL>"
        f"<CoverageName>{escape(coverage)}</CoverageName>"
        f"<Version>{escape(GA_SRTM_WCS_VERSION)}</Version>"
        "</WCS_GDAL>"
    )

    logger.info("Requesting DEM window %s from GA SRTM WCS", geo_bounds)
    # GDAL's WCS driver caches a DescribeCoverage (.DC.xml) next to the
    # service descriptor, so the descriptor must be a real file in a
    # writable directory rather than an inline XML string.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        descriptor = Path(tmp_dir) / "dem_wcs.xml"
        descriptor.write_text(wcs_xml, encoding="utf-8")
        return _read_wcs_window(str(descriptor), geo_bounds)


def _read_wcs_window(
    descriptor_path: str,
    geo_bounds: tuple[float, float, float, float],
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Open a GDAL WCS descriptor and read the AOI window.

    Args:
        descriptor_path: Path to a GDAL ``<WCS_GDAL>`` descriptor file.
        geo_bounds: AOI bounds in the coverage CRS (geographic degrees).

    Returns:
        ``(data, grid)`` for the covering window.

    Raises:
        ValueError: If the AOI does not intersect the coverage.
        RuntimeError: If the WCS request fails.
    """
    from bal_toolbox_qgis.balcore import _rio as rasterio
    from bal_toolbox_qgis.balcore._rio.errors import RasterioError
    from bal_toolbox_qgis.balcore._rio.windows import from_bounds

    from bal_toolbox_qgis.balcore.raster import (
        _apply_source_nodata,
        _clamp_window,
        _cover_window,
    )

    try:
        with rasterio.open(descriptor_path) as src:
            window = from_bounds(*geo_bounds, transform=src.transform)
            window = _cover_window(window)
            read_window = _clamp_window(window, src.width, src.height)
            if read_window is None:
                raise ValueError(
                    "Area of interest does not intersect the national DEM "
                    "coverage; check the AOI bounds and CRS."
                )
            data = src.read(1, window=read_window).astype(np.float64)
            _apply_source_nodata(data, src.nodata)
            transform_w = src.window_transform(read_window)
            grid = RasterGrid(
                transform=transform_w,
                crs=src.crs,
                pixel_width=abs(src.transform.a),
                pixel_height=abs(src.transform.e),
                shape=(int(read_window.height), int(read_window.width)),
            )
    except ValueError:
        raise
    except (RasterioError, OSError) as err:  # pragma: no cover - network/service
        raise RuntimeError(
            f"Failed to fetch the national DEM from the WCS service: {err}"
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
