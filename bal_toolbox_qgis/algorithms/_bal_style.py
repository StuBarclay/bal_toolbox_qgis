"""Default styling for the maximum-BAL output raster.

The compute core writes the six AS 3959:2018 attack-level codes as raster
pixel values -- ``0`` (BAL-LOW), ``12.5``, ``19``, ``29``, ``40`` and ``100``
(BAL-FZ, the Flame Zone), with :data:`bal_toolbox_qgis.balcore.tables.NODATA`
(``-99``) outside the assessed area. Without a renderer QGIS shows those as a
meaningless grey ramp, so both the Processing algorithm and the friendly
dialog apply the categorised palette defined here when the layer is loaded.

:data:`BAL_CLASSES` is deliberately a plain data table with no ``qgis`` import
at module load, so the value/label/colour mapping can be unit-tested outside
QGIS (and checked against the core's :mod:`~bal_toolbox_qgis.balcore.tables`).
The renderer itself is built lazily in :func:`apply_bal_style`, which imports
the ``qgis`` runtime only when actually called inside QGIS.
"""

from __future__ import annotations

from typing import Any

#: The BAL classes in ascending exposure order as ``(value, label, hex)``.
#: ``value`` is the raster pixel value the core writes; ``hex`` is a
#: ColorBrewer-style yellow-to-dark-red ramp with green for the low class.
BAL_CLASSES: tuple[tuple[float, str, str], ...] = (
    (0.0, "BAL-LOW", "#1a9850"),
    (12.5, "BAL-12.5", "#fee08b"),
    (19.0, "BAL-19", "#fdae61"),
    (29.0, "BAL-29", "#f46d43"),
    (40.0, "BAL-40", "#d73027"),
    (100.0, "BAL-FZ", "#7f0000"),
)


def apply_bal_style(layer: Any) -> bool:
    """Apply the categorised BAL palette to a loaded raster layer.

    Builds a :class:`QgsPalettedRasterRenderer` from :data:`BAL_CLASSES` and
    sets it on ``layer``. Pixels that match no class (e.g. NODATA) are left
    unrendered (transparent), which is the desired behaviour outside the
    assessed area.

    Args:
        layer: The raster layer to style. ``None`` or a non-raster/invalid
            layer is ignored so callers need not pre-check.

    Returns:
        ``True`` if the palette was applied, ``False`` otherwise.
    """
    # Imported lazily: this module is imported by qgis-free unit tests, which
    # exercise BAL_CLASSES without a QGIS runtime available.
    from qgis.core import QgsPalettedRasterRenderer, QgsRasterLayer
    from qgis.PyQt.QtGui import QColor

    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return False
    provider = layer.dataProvider()
    if provider is None:
        return False
    classes = [
        QgsPalettedRasterRenderer.Class(value, QColor(hex_color), label)
        for value, label, hex_color in BAL_CLASSES
    ]
    renderer = QgsPalettedRasterRenderer(provider, 1, classes)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return True
