"""Equivalence tests for the shim ``Affine`` against the ``affine`` package.

These need only ``numpy`` and the ``affine`` reference (a pure-Python
rasterio dependency), so they run in any environment -- no GDAL required.
"""

from __future__ import annotations

import affine as _affine
import pytest

from .pureshim import Affine

# A spread of transforms: north-up, south-up, rotated, translated, scaled.
CASES = [
    (30.0, 0.0, 500000.0, 0.0, -30.0, 6100000.0),  # typical north-up MGA
    (0.00025, 0.0, 138.0, 0.0, -0.00025, -34.0),  # geographic north-up
    (10.0, 2.0, 100.0, -3.0, -12.0, 200.0),  # rotated/sheared
    (1.0, 0.0, 0.0, 0.0, 1.0, 0.0),  # identity-like
    (25.0, 0.0, -1000.0, 0.0, 25.0, -2000.0),  # south-up (positive e)
]


@pytest.mark.parametrize("coeffs", CASES)
def test_construction_and_iteration(coeffs: tuple[float, ...]) -> None:
    ours = Affine(*coeffs)
    ref = _affine.Affine(*coeffs)
    assert tuple(ours) == pytest.approx(tuple(ref))
    # matches_grid relies on tuple(t)[:6] == (a, b, c, d, e, f)
    assert tuple(ours)[:6] == pytest.approx(coeffs)
    assert (ours.a, ours.b, ours.c, ours.d, ours.e, ours.f) == pytest.approx(
        (ref.a, ref.b, ref.c, ref.d, ref.e, ref.f)
    )


@pytest.mark.parametrize("coeffs", CASES)
def test_to_and_from_gdal_roundtrip(coeffs: tuple[float, ...]) -> None:
    ours = Affine(*coeffs)
    ref = _affine.Affine(*coeffs)
    assert tuple(ours.to_gdal()) == pytest.approx(tuple(ref.to_gdal()))
    rebuilt = Affine.from_gdal(*ours.to_gdal())
    assert tuple(rebuilt) == pytest.approx(tuple(ours))
    ref_rebuilt = _affine.Affine.from_gdal(*ref.to_gdal())
    assert tuple(rebuilt) == pytest.approx(tuple(ref_rebuilt))


@pytest.mark.parametrize("coeffs", CASES)
def test_point_application(coeffs: tuple[float, ...]) -> None:
    ours = Affine(*coeffs)
    ref = _affine.Affine(*coeffs)
    for col, row in [(0, 0), (1, 0), (0, 1), (12.5, 7.25), (-3, 4)]:
        assert ours * (col, row) == pytest.approx(ref * (col, row))


@pytest.mark.parametrize("coeffs", CASES)
def test_composition(coeffs: tuple[float, ...]) -> None:
    ours = Affine(*coeffs)
    ref = _affine.Affine(*coeffs)
    shift_ours = Affine.translation(3.0, -5.0)
    shift_ref = _affine.Affine.translation(3.0, -5.0)
    assert tuple(ours * shift_ours) == pytest.approx(tuple(ref * shift_ref))
    scale_ours = Affine.scale(2.0, 0.5)
    scale_ref = _affine.Affine.scale(2.0, 0.5)
    assert tuple(ours * scale_ours) == pytest.approx(tuple(ref * scale_ref))


@pytest.mark.parametrize("coeffs", CASES)
def test_inversion_matches_and_roundtrips(coeffs: tuple[float, ...]) -> None:
    ours = Affine(*coeffs)
    ref = _affine.Affine(*coeffs)
    if abs(ref.determinant) < 1e-9:
        pytest.skip("degenerate")
    inv_ours = ~ours
    inv_ref = ~ref
    assert tuple(inv_ours) == pytest.approx(tuple(inv_ref), abs=1e-9, rel=1e-9)
    # ~A * (A * p) == p
    for col, row in [(0, 0), (5, 9), (123.4, -56.7)]:
        x, y = ours * (col, row)
        back = inv_ours * (x, y)
        assert back == pytest.approx((col, row), abs=1e-6)


def test_identity_translation_scale() -> None:
    assert tuple(Affine.identity()) == pytest.approx(tuple(_affine.Affine.identity()))
    assert tuple(Affine.translation(7, -2)) == pytest.approx(
        tuple(_affine.Affine.translation(7, -2))
    )
    assert tuple(Affine.scale(4)) == pytest.approx(tuple(_affine.Affine.scale(4)))
    assert tuple(Affine.scale(4, 9)) == pytest.approx(tuple(_affine.Affine.scale(4, 9)))


def test_equality_and_hash() -> None:
    a = Affine(30, 0, 500000, 0, -30, 6100000)
    b = Affine(30, 0, 500000, 0, -30, 6100000)
    c = Affine(30, 0, 500001, 0, -30, 6100000)
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


def test_determinant_matches() -> None:
    for coeffs in CASES:
        assert Affine(*coeffs).determinant == pytest.approx(
            _affine.Affine(*coeffs).determinant
        )


def test_inversion_raises_on_degenerate() -> None:
    with pytest.raises(ValueError):
        ~Affine(0, 0, 1, 0, 0, 1)
