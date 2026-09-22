"""Pure-Python request geometry and validation for the GA SRTM WCS.

The maths that turns an area of interest into a concrete WCS ``GetCoverage``
request -- clamping to the published coverage extent, snapping to whole SRTM
cells and computing the pixel dimensions -- lives here, deliberately free of
the GDAL-backed raster shim. That keeps it importable and unit-testable
without a GDAL/QGIS runtime; the actual HTTP download and raster read live in
:mod:`bal_toolbox_qgis.balcore.dem_source`.
"""

from __future__ import annotations

import math
from typing import Final

#: Native cell size of the SRTM 1-second product in degrees (~30 m).
SRTM_CELL_SIZE_DEG: Final[float] = 1.0 / 3600.0

#: WGS84 bounds ``(xmin, ymin, xmax, ymax)`` of the GA SRTM 1-Second coverage,
#: from the service GetCapabilities lonLat envelope. West/south are the true
#: (half-cell-offset) grid origin, so the span is exactly 41 x 34 degrees
#: (147600 x 122400 cells) and cell-aligned windows land on real pixel edges.
COVERAGE_WGS84_BOUNDS: Final[tuple[float, float, float, float]] = (
    112.999861111111,
    -44.000138888889,
    153.999861111111,
    -10.000138888889,
)

#: TIFF file signatures (little- and big-endian byte order marks).
_TIFF_MAGIC: Final[tuple[bytes, ...]] = (b"II*\x00", b"MM\x00*")


def align_request_window(
    geo_bounds: tuple[float, float, float, float],
    coverage_bounds: tuple[float, float, float, float] = COVERAGE_WGS84_BOUNDS,
    cell: float = SRTM_CELL_SIZE_DEG,
) -> tuple[tuple[float, float, float, float], int, int]:
    """Snap AOI bounds outward to whole SRTM cells, clamped to the coverage.

    Args:
        geo_bounds: AOI bounds ``(xmin, ymin, xmax, ymax)`` in geographic
            degrees (EPSG:4326); ordering is normalised defensively.
        coverage_bounds: The coverage extent to clamp against.
        cell: The coverage cell size in degrees.

    Returns:
        ``(request_bounds, width, height)`` where ``request_bounds`` is the
        cell-aligned ``(xmin, ymin, xmax, ymax)`` to request and
        ``width``/``height`` are the integer pixel dimensions.

    Raises:
        ValueError: If the AOI does not intersect the coverage extent.
    """
    west0, south0, east0, north0 = coverage_bounds
    xmin, ymin, xmax, ymax = geo_bounds
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    if ymax < ymin:
        ymin, ymax = ymax, ymin

    cols_total = int(round((east0 - west0) / cell))
    rows_total = int(round((north0 - south0) / cell))

    i0 = max(0, int(math.floor((xmin - west0) / cell)))
    i1 = min(cols_total, int(math.ceil((xmax - west0) / cell)))
    j0 = max(0, int(math.floor((ymin - south0) / cell)))
    j1 = min(rows_total, int(math.ceil((ymax - south0) / cell)))

    if i1 <= i0 or j1 <= j0:
        raise ValueError(
            "Area of interest does not intersect the national DEM coverage; "
            "check the AOI bounds and CRS."
        )

    request_bounds = (
        west0 + i0 * cell,
        south0 + j0 * cell,
        west0 + i1 * cell,
        south0 + j1 * cell,
    )
    return request_bounds, i1 - i0, j1 - j0


def build_getcoverage_url(
    service_url: str,
    coverage: str,
    version: str,
    request_bounds: tuple[float, float, float, float],
    width: int,
    height: int,
) -> str:
    """Build a WCS 1.0.0 ``GetCoverage`` KVP URL for a GeoTIFF window.

    Args:
        service_url: The WCS endpoint (may already end with ``?`` or carry a
            query string).
        coverage: The WCS coverage identifier.
        version: The WCS protocol version (``"1.0.0"`` for this service).
        request_bounds: Cell-aligned ``(xmin, ymin, xmax, ymax)`` in EPSG:4326
            (WCS 1.0.0 uses longitude/latitude, i.e. x/y, axis order).
        width: Grid width in pixels.
        height: Grid height in pixels.

    Returns:
        The fully-formed request URL.
    """
    xmin, ymin, xmax, ymax = request_bounds
    bbox = f"{xmin:.10f},{ymin:.10f},{xmax:.10f},{ymax:.10f}"
    if service_url.endswith("?") or service_url.endswith("&"):
        separator = ""
    elif "?" in service_url:
        separator = "&"
    else:
        separator = "?"
    return (
        f"{service_url}{separator}SERVICE=WCS&VERSION={version}"
        f"&REQUEST=GetCoverage&COVERAGE={coverage}&CRS=EPSG:4326"
        f"&BBOX={bbox}&WIDTH={width}&HEIGHT={height}&FORMAT=GeoTIFF"
    )


def looks_like_tiff(payload: bytes) -> bool:
    """Return whether ``payload`` begins with a TIFF/GeoTIFF signature."""
    return payload[:4] in _TIFF_MAGIC


def error_detail(payload: bytes, limit: int = 300) -> str:
    """Extract a short, single-line message from a service error payload.

    WCS servers return XML/HTML ``ServiceExceptionReport`` bodies on failure;
    this collapses the whitespace and truncates so the text can be folded into
    a ``RuntimeError`` message without dumping a whole document.

    Args:
        payload: The raw response body.
        limit: Maximum length of the returned string.

    Returns:
        A trimmed, single-line rendering of the payload.
    """
    text = " ".join(payload.decode("utf-8", errors="replace").split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text
