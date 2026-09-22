"""A minimal, dependency-free clone of :class:`affine.Affine`.

rasterio re-exports ``affine.Affine`` as ``rasterio.Affine`` and uses it
everywhere a raster transform is passed around. QGIS bundles GDAL but not
the tiny pure-Python ``affine`` package, so the vendored core would gain a
new third-party dependency if it kept importing it. This class reproduces
exactly the behaviour the core relies on:

* construction as ``Affine(a, b, c, d, e, f)`` with the same coefficient
  meaning (``x = a*col + b*row + c``; ``y = d*col + e*row + f``);
* iteration yielding the nine matrix elements, so ``tuple(t)[:6]`` gives
  ``(a, b, c, d, e, f)`` (used by ``matches_grid``);
* multiplication with another transform (composition) and with a point;
* inversion via ``~transform`` (used by window maths);
* ``to_gdal()`` / ``from_gdal()`` bridging GDAL's geotransform ordering.

The convention matches both ``affine`` and GDAL: a GDAL geotransform
``(c, a, b, f, d, e)`` maps to ``Affine(a, b, c, d, e, f)``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Union, overload

Number = Union[int, float]


class Affine:
    """A 2-D affine transformation matrix (identical API to ``affine.Affine``)."""

    __slots__ = ("a", "b", "c", "d", "e", "f")

    #: Machine-epsilon-scale tolerance used for equality, matching the
    #: ``affine`` package's default ``__eq__`` (exact) but tolerant repr.
    def __init__(
        self,
        a: Number,
        b: Number,
        c: Number,
        d: Number,
        e: Number,
        f: Number,
    ) -> None:
        self.a = float(a)
        self.b = float(b)
        self.c = float(c)
        self.d = float(d)
        self.e = float(e)
        self.f = float(f)

    # -- alternate constructors ------------------------------------------
    @classmethod
    def identity(cls) -> "Affine":
        """Return the identity transform."""
        return cls(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

    @classmethod
    def translation(cls, xoff: Number, yoff: Number) -> "Affine":
        """Return a translation transform by ``(xoff, yoff)``."""
        return cls(1.0, 0.0, float(xoff), 0.0, 1.0, float(yoff))

    @classmethod
    def scale(cls, *scaling: Number) -> "Affine":
        """Return a scaling transform.

        Accepts one factor (uniform) or two (x then y), matching
        ``affine.Affine.scale``.
        """
        if len(scaling) == 1:
            sx = sy = float(scaling[0])
        elif len(scaling) == 2:
            sx, sy = float(scaling[0]), float(scaling[1])
        else:  # pragma: no cover - defensive
            raise TypeError("scale() takes one or two arguments")
        return cls(sx, 0.0, 0.0, 0.0, sy, 0.0)

    @classmethod
    def from_gdal(
        cls,
        c: Number,
        a: Number,
        b: Number,
        f: Number,
        d: Number,
        e: Number,
    ) -> "Affine":
        """Build a transform from GDAL geotransform coefficients.

        GDAL orders its geotransform as ``(c, a, b, f, d, e)`` -- origin
        x, pixel width, row rotation, origin y, column rotation, pixel
        height -- which is the argument order here.
        """
        return cls(a, b, c, d, e, f)

    def to_gdal(self) -> tuple[float, float, float, float, float, float]:
        """Return this transform as a GDAL geotransform ``(c, a, b, f, d, e)``."""
        return (self.c, self.a, self.b, self.f, self.d, self.e)

    # -- sequence protocol -----------------------------------------------
    def __iter__(self) -> Iterator[float]:
        yield self.a
        yield self.b
        yield self.c
        yield self.d
        yield self.e
        yield self.f
        yield 0.0
        yield 0.0
        yield 1.0

    def __getitem__(self, index: int) -> float:
        return tuple(self)[index]

    def __len__(self) -> int:
        return 9

    # matrix element aliases used by the ``affine`` API ------------------
    @property
    def g(self) -> float:
        return 0.0

    @property
    def h(self) -> float:
        return 0.0

    @property
    def i(self) -> float:
        return 1.0

    @property
    def xoff(self) -> float:
        """Translation in x (the ``c`` coefficient)."""
        return self.c

    @property
    def yoff(self) -> float:
        """Translation in y (the ``f`` coefficient)."""
        return self.f

    @property
    def determinant(self) -> float:
        """Return the determinant of the linear part."""
        return self.a * self.e - self.b * self.d

    # -- arithmetic ------------------------------------------------------
    @overload
    def __mul__(self, other: "Affine") -> "Affine": ...

    @overload
    def __mul__(self, other: "tuple[Number, Number]") -> "tuple[float, float]": ...

    def __mul__(
        self, other: "Affine | tuple[Number, Number]"
    ) -> "Affine | tuple[float, float]":
        """Compose with another transform, or apply to a point.

        ``A * B`` returns the transform equivalent to applying ``B`` then
        ``A``. ``A * (x, y)`` returns the transformed point tuple.
        """
        if isinstance(other, Affine):
            sa, sb, sc = self.a, self.b, self.c
            sd, se, sf = self.d, self.e, self.f
            oa, ob, oc = other.a, other.b, other.c
            od, oe, of = other.d, other.e, other.f
            return Affine(
                sa * oa + sb * od,
                sa * ob + sb * oe,
                sa * oc + sb * of + sc,
                sd * oa + se * od,
                sd * ob + se * oe,
                sd * oc + se * of + sf,
            )
        try:
            x, y = other
        except (TypeError, ValueError):
            return NotImplemented
        return (self.a * x + self.b * y + self.c, self.d * x + self.e * y + self.f)

    def __rmul__(
        self, other: "Affine | tuple[Number, Number]"
    ) -> "Affine | tuple[float, float]":
        # Points are applied on the right in the ``affine`` API; only the
        # matrix @ point form is meaningful here.
        return self.__mul__(other)

    def __invert__(self) -> "Affine":
        """Return the inverse transform (``~A``)."""
        det = self.determinant
        if det == 0.0:
            raise ValueError("Cannot invert a degenerate (non-invertible) transform")
        idet = 1.0 / det
        ra = self.e * idet
        rb = -self.b * idet
        rd = -self.d * idet
        re = self.a * idet
        rc = -(ra * self.c + rb * self.f)
        rf = -(rd * self.c + re * self.f)
        return Affine(ra, rb, rc, rd, re, rf)

    # -- comparison / hashing / repr -------------------------------------
    def __eq__(self, other: object) -> bool:
        if isinstance(other, Affine):
            return tuple(self) == tuple(other)
        if isinstance(other, (tuple, list)) and len(other) in (6, 9):
            return tuple(self)[: len(other)] == tuple(other)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self) -> int:
        return hash(tuple(self))

    def __repr__(self) -> str:
        return (
            "Affine({:.6g}, {:.6g}, {:.6g},\n"
            "       {:.6g}, {:.6g}, {:.6g})"
        ).format(self.a, self.b, self.c, self.d, self.e, self.f)
