"""Equivalence tests for the shim window maths against ``rasterio.windows``.

``rasterio.windows``/``rasterio.transform`` are backed by rasterio's own
bundled GDAL, so these run wherever ``rasterio`` is installed. They pin the
sign/rounding behaviour the core depends on (``from_bounds``,
``round_offsets``/``round_lengths``, and the window ``transform``).
"""

from __future__ import annotations

import affine as _affine
import pytest
import rasterio.transform as rio_transform
import rasterio.windows as rio_windows

from .pureshim import (
    Affine,
    Window,
)
from .pureshim import (
    errors as shim_errors,
)
from .pureshim import (
    transform as shim_transform,
)
from .pureshim import (
    windows as shim_windows,
)

# North-up transforms are consistent for ``from_bounds`` (rasterio requires
# ``(right-left)/a >= 0`` and ``(bottom-top)/e >= 0``). Each is paired with
# bounds in its own coordinate space.
NORTH_UP_CASES = [
    (
        (30.0, 0.0, 500000.0, 0.0, -30.0, 6100000.0),  # north-up MGA (metres)
        (500300.0, 6099100.0, 500950.0, 6099880.0),
    ),
    (
        (0.00025, 0.0, 138.0, 0.0, -0.00025, -34.0),  # geographic north-up
        (138.01, -34.05, 138.04, -34.02),
    ),
]

# South-up transform (positive ``e``). rasterio's ``from_bounds`` rejects
# this as inconsistent with north-up bounds; the shim must do the same.
SOUTH_UP_COEFFS = (25.0, 0.0, 300000.0, 0.0, 25.0, 5000000.0)
SOUTH_UP_BOUNDS = (300100.0, 5000100.0, 300700.0, 5000700.0)

# Every transform is valid for the coordinate-only helpers (array_bounds and
# the window transform), which never invert the transform.
ALL_COEFFS = [c for c, _ in NORTH_UP_CASES] + [SOUTH_UP_COEFFS]


@pytest.mark.parametrize("coeffs,bounds", NORTH_UP_CASES)
def test_from_bounds_matches_rasterio(
    coeffs: tuple[float, ...], bounds: tuple[float, ...]
) -> None:
    ours = shim_windows.from_bounds(*bounds, transform=Affine(*coeffs))
    ref = rio_windows.from_bounds(*bounds, transform=_affine.Affine(*coeffs))
    assert ours.col_off == pytest.approx(ref.col_off, abs=1e-6)
    assert ours.row_off == pytest.approx(ref.row_off, abs=1e-6)
    assert ours.width == pytest.approx(ref.width, abs=1e-6)
    assert ours.height == pytest.approx(ref.height, abs=1e-6)


def test_from_bounds_rejects_south_up_like_rasterio() -> None:
    # A south-up transform is inconsistent with north-up bounds; both the
    # shim and rasterio raise a WindowError rather than return a window.
    with pytest.raises(shim_errors.WindowError):
        shim_windows.from_bounds(*SOUTH_UP_BOUNDS, transform=Affine(*SOUTH_UP_COEFFS))
    with pytest.raises(rio_windows.WindowError):
        rio_windows.from_bounds(
            *SOUTH_UP_BOUNDS, transform=_affine.Affine(*SOUTH_UP_COEFFS)
        )


@pytest.mark.parametrize("coeffs,bounds", NORTH_UP_CASES)
def test_round_offsets_then_lengths(
    coeffs: tuple[float, ...], bounds: tuple[float, ...]
) -> None:
    ours = shim_windows.from_bounds(*bounds, transform=Affine(*coeffs))
    ref = rio_windows.from_bounds(*bounds, transform=_affine.Affine(*coeffs))
    ours_r = ours.round_offsets().round_lengths()
    ref_r = ref.round_offsets().round_lengths()
    assert (ours_r.col_off, ours_r.row_off) == (ref_r.col_off, ref_r.row_off)
    assert (ours_r.width, ours_r.height) == (ref_r.width, ref_r.height)


@pytest.mark.parametrize("coeffs", ALL_COEFFS)
def test_window_transform_matches(coeffs: tuple[float, ...]) -> None:
    win = Window(3, 7, 10, 12)
    ref_win = rio_windows.Window(3, 7, 10, 12)
    ours = shim_windows.transform(win, Affine(*coeffs))
    ref = rio_windows.transform(ref_win, _affine.Affine(*coeffs))
    assert tuple(ours)[:6] == pytest.approx(tuple(ref)[:6])


@pytest.mark.parametrize("coeffs", ALL_COEFFS)
def test_array_bounds_matches(coeffs: tuple[float, ...]) -> None:
    height, width = 200, 350
    ours = shim_transform.array_bounds(height, width, Affine(*coeffs))
    ref = rio_transform.array_bounds(height, width, _affine.Affine(*coeffs))
    assert ours == pytest.approx(ref)


def test_round_offsets_lengths_match_rasterio_ignoring_op() -> None:
    # rasterio >= 1.3 ignores the deprecated ``op`` argument: offsets floor
    # ``off + 0.1`` and lengths floor ``length + 0.5``. Pin the shim to the
    # exact values rasterio produces.
    ours = Window(1.2, 2.8, 5.4, 6.1)
    ref = rio_windows.Window(1.2, 2.8, 5.4, 6.1)

    our_off = ours.round_offsets()
    ref_off = ref.round_offsets()
    assert (our_off.col_off, our_off.row_off) == (ref_off.col_off, ref_off.row_off)

    our_len = ours.round_lengths()
    ref_len = ref.round_lengths()
    assert (our_len.width, our_len.height) == (ref_len.width, ref_len.height)
