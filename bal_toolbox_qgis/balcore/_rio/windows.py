"""Window maths mirroring the subset of :mod:`rasterio.windows` used.

The core uses :class:`Window` (its ``col_off``/``row_off``/``width``/
``height`` attributes plus ``round_offsets``/``round_lengths``),
:func:`from_bounds`, and the module-level :func:`transform` that maps a
window into a full-grid affine transform.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

from .affine import Affine
from .errors import WindowError

__all__ = ["Window", "from_bounds", "transform"]


class Window:
    """A rectangular pixel window, compatible with ``rasterio.windows.Window``.

    Offsets and lengths are kept as floats until explicitly rounded.
    """

    __slots__ = ("col_off", "row_off", "width", "height")

    def __init__(
        self,
        col_off: float,
        row_off: float,
        width: float,
        height: float,
    ) -> None:
        self.col_off = col_off
        self.row_off = row_off
        self.width = width
        self.height = height

    def round_offsets(self, op: Any = None, pixel_precision: int | None = None, **kwds: Any) -> "Window":
        """Return a copy with the offsets floored to the preceding pixel.

        Mirrors :meth:`rasterio.windows.Window.round_offsets` (rasterio
        >= 1.3): the ``op`` argument is deprecated and ignored, and the
        offsets are always taken as ``floor(off + 0.1)``.
        """
        return Window(
            math.floor(self.col_off + 0.1),
            math.floor(self.row_off + 0.1),
            self.width,
            self.height,
        )

    def round_lengths(self, op: Any = None, pixel_precision: int | None = None, **kwds: Any) -> "Window":
        """Return a copy with width/height rounded to the nearest pixel.

        Mirrors :meth:`rasterio.windows.Window.round_lengths` (rasterio
        >= 1.3): the ``op`` argument is deprecated and ignored, and the
        lengths are always taken as ``floor(length + 0.5)``.
        """
        return Window(
            self.col_off,
            self.row_off,
            math.floor(self.width + 0.5),
            math.floor(self.height + 0.5),
        )

    @property
    def col_stop(self) -> float:
        return self.col_off + self.width

    @property
    def row_stop(self) -> float:
        return self.row_off + self.height

    def __iter__(self) -> Iterator[tuple[float, float]]:
        # rasterio's Window iterates as ((row_off, row_stop), (col_off, col_stop)).
        yield (self.row_off, self.row_stop)
        yield (self.col_off, self.col_stop)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Window):
            return NotImplemented
        return (
            self.col_off == other.col_off
            and self.row_off == other.row_off
            and self.width == other.width
            and self.height == other.height
        )

    def __repr__(self) -> str:
        return (
            f"Window(col_off={self.col_off}, row_off={self.row_off}, "
            f"width={self.width}, height={self.height})"
        )


def from_bounds(
    left: float,
    bottom: float,
    right: float,
    top: float,
    transform: Affine,
) -> Window:
    """Return the window covering world-space bounds under ``transform``.

    Mirrors :func:`rasterio.windows.from_bounds` for axis-aligned
    (north-up or south-up) transforms.

    Args:
        left: Minimum x of the bounds.
        bottom: Minimum y of the bounds.
        right: Maximum x of the bounds.
        top: Maximum y of the bounds.
        transform: The reference grid's affine transform.

    Returns:
        A :class:`Window` in pixel coordinates.

    Raises:
        WindowError: If ``transform`` is not an :class:`Affine`, is
            degenerate, or is inconsistent with the requested bounds
            (e.g. a south-up transform) -- matching rasterio.
    """
    if not isinstance(transform, Affine):
        raise WindowError("A transform object is required to calculate the window")
    if (right - left) / transform.a < 0:
        raise WindowError("Bounds and transform are inconsistent")
    if (bottom - top) / transform.e < 0:
        raise WindowError("Bounds and transform are inconsistent")

    try:
        inverse = ~transform
    except ValueError as exc:  # degenerate transform
        raise WindowError(str(exc)) from exc

    # Evaluate all four corners (matches rasterio's rowcol-based method,
    # so rotated transforms behave identically, not just axis-aligned ones).
    xs = (left, right, right, left)
    ys = (top, top, bottom, bottom)
    cols: list[float] = []
    rows: list[float] = []
    for x, y in zip(xs, ys):
        col, row = inverse * (x, y)
        cols.append(col)
        rows.append(row)

    col_start, col_stop = min(cols), max(cols)
    row_start, row_stop = min(rows), max(rows)
    return Window(
        col_start,
        row_start,
        max(col_stop - col_start, 0.0),
        max(row_stop - row_start, 0.0),
    )


def transform(window: Window, transform: Affine) -> Affine:
    """Return the affine transform for a window's own pixel grid.

    Mirrors :func:`rasterio.windows.transform`: the transform that maps
    pixel coordinates *within* ``window`` to world coordinates, i.e. the
    parent ``transform`` shifted by the window's offset.

    Args:
        window: The window to derive a transform for.
        transform: The parent grid's affine transform.

    Returns:
        The window's affine transform.
    """
    return transform * Affine.translation(window.col_off, window.row_off)
