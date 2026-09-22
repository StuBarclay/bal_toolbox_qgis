"""Tests for the pure WCS request maths behind the national DEM fetch.

These exercise :mod:`bal_toolbox_qgis.balcore._wcs_request`, which is
deliberately free of the GDAL-backed shim (only ``math`` and ``typing``), so
the request-window snapping, URL construction and payload validation all run
in the plain sandbox and in CI -- no ``osgeo``/``qgis`` runtime required. The
live HTTP download and raster read in ``dem_source`` are exercised only in a
real QGIS run.
"""

from __future__ import annotations

import pytest
from bal_toolbox_qgis.balcore._wcs_request import (
    COVERAGE_WGS84_BOUNDS,
    SRTM_CELL_SIZE_DEG,
    align_request_window,
    build_getcoverage_url,
    error_detail,
    looks_like_tiff,
)

# A small area of interest well inside the coverage (Adelaide Hills).
_ADELAIDE = (138.70, -34.95, 138.75, -34.90)
_CELL = SRTM_CELL_SIZE_DEG


def test_align_request_window_snaps_outward_and_aligns_to_cells() -> None:
    """The request window contains the AOI and lands on whole cell edges."""
    (xmin, ymin, xmax, ymax), width, height = align_request_window(_ADELAIDE)

    # Snapped outward: the window fully contains the requested AOI.
    assert xmin <= _ADELAIDE[0]
    assert ymin <= _ADELAIDE[1]
    assert xmax >= _ADELAIDE[2]
    assert ymax >= _ADELAIDE[3]

    # Positive, integer pixel dimensions consistent with the bounds/cell size.
    assert width > 0
    assert height > 0
    assert width == round((xmax - xmin) / _CELL)
    assert height == round((ymax - ymin) / _CELL)

    # A ~0.05 degree AOI is ~180 cells wide/high at 1-second resolution.
    assert 175 <= width <= 190
    assert 175 <= height <= 190


def test_align_request_window_normalises_swapped_bounds() -> None:
    """Reversed min/max inputs give the same window as ordered inputs."""
    ordered = align_request_window(_ADELAIDE)
    swapped = align_request_window((138.75, -34.90, 138.70, -34.95))
    assert swapped == ordered


def test_align_request_window_clamps_to_coverage_extent() -> None:
    """An AOI overhanging the NE corner is clamped to the coverage edge."""
    _, _, east0, north0 = COVERAGE_WGS84_BOUNDS
    # Straddle the eastern and northern edges of the coverage.
    (_, _, xmax, ymax), _, _ = align_request_window((150.0, -12.0, 160.0, -8.0))
    assert xmax <= east0 + 1e-9
    assert ymax <= north0 + 1e-9
    # And it should reach (within a cell of) those edges, not fall short.
    assert xmax >= east0 - _CELL
    assert ymax >= north0 - _CELL


def test_align_request_window_rejects_disjoint_aoi() -> None:
    """An AOI entirely outside the coverage raises a clear ValueError."""
    with pytest.raises(ValueError, match="does not intersect"):
        align_request_window((0.0, 40.0, 1.0, 41.0))  # somewhere in Europe


def test_build_getcoverage_url_has_expected_kvp() -> None:
    """The URL carries the WCS 1.0.0 GetCoverage parameters in x/y order."""
    bounds = (138.7001, -34.9502, 138.7503, -34.9004)
    url = build_getcoverage_url(
        "https://example/WCSServer?", "1", "1.0.0", bounds, 181, 179
    )
    assert "SERVICE=WCS" in url
    assert "VERSION=1.0.0" in url
    assert "REQUEST=GetCoverage" in url
    assert "COVERAGE=1" in url
    assert "CRS=EPSG:4326" in url
    assert "WIDTH=181" in url
    assert "HEIGHT=181" not in url  # height is distinct
    assert "HEIGHT=179" in url
    assert url.endswith("FORMAT=GeoTIFF")
    # BBOX must be xmin,ymin,xmax,ymax (lon/lat) for WCS 1.0.0.
    assert "BBOX=138.7001000000,-34.9502000000,138.7503000000,-34.9004000000" in url
    # No doubled separators after the trailing '?'.
    assert "WCSServer?SERVICE=WCS" in url


def test_build_getcoverage_url_inserts_separator_when_needed() -> None:
    """A bare endpoint (no ``?``) gets a ``?`` before the query string."""
    url = build_getcoverage_url(
        "https://example/WCSServer", "1", "1.0.0", (0.0, 0.0, 1.0, 1.0), 10, 10
    )
    assert "WCSServer?SERVICE=WCS" in url


def test_looks_like_tiff_accepts_both_byte_orders() -> None:
    """Little- and big-endian TIFF markers pass; other content does not."""
    assert looks_like_tiff(b"II*\x00rest of file")
    assert looks_like_tiff(b"MM\x00*rest of file")
    assert not looks_like_tiff(b"<?xml version='1.0'?><ServiceException/>")
    assert not looks_like_tiff(b"")


def test_error_detail_collapses_whitespace_and_truncates() -> None:
    """A verbose XML error is reduced to a short single line."""
    body = b"<Report>\n   http.400:\n\t Bad   Request \n</Report>"
    detail = error_detail(body, limit=20)
    assert "\n" not in detail
    assert "  " not in detail  # no runs of whitespace
    assert len(detail) <= 23  # limit + the "..." ellipsis
    assert detail.endswith("...")
    # A short body is returned intact (no ellipsis).
    assert error_detail(b"http.400: Bad Request") == "http.400: Bad Request"
