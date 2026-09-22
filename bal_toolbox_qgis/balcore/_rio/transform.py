"""Transform helpers mirroring the subset of :mod:`rasterio.transform` used.

The vendored core imports :class:`Affine` from here (via
``from ..._rio.transform import Affine`` after the import rewrite) and calls
:func:`array_bounds`.
"""

from __future__ import annotations

from .affine import Affine

__all__ = ["Affine", "array_bounds"]


def array_bounds(height: int, width: int, transform: Affine) -> tuple[float, float, float, float]:
    """Return the ``(left, bottom, right, top)`` bounds of an array.

    Mirrors :func:`rasterio.transform.array_bounds`: the outer bounds of a
    raster of ``height`` rows by ``width`` columns under ``transform``.

    Args:
        height: Number of rows.
        width: Number of columns.
        transform: The array's affine transform.

    Returns:
        ``(left, bottom, right, top)`` in the transform's coordinate space.
    """
    left, top = transform * (0.0, 0.0)
    right, bottom = transform * (float(width), float(height))
    return left, bottom, right, top
