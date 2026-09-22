"""bal_toolbox_qgis -- QGIS plugin: AS 3959:2018 bushfire attack level toolbox.

Top-level plugin package. It is deliberately kept import-light: importing
``bal_toolbox_qgis`` must NOT pull in PyQt or the ``qgis`` runtime, so that
the pure-Python compute core (:mod:`bal_toolbox_qgis.balcore`) remains
importable and testable outside QGIS. QGIS constructs the plugin by
calling :func:`classFactory`, which imports the GUI layer lazily.
"""

from __future__ import annotations

import sys

#: Plugin version (kept in sync with ``metadata.txt``).
__version__ = "0.1.6"

#: The compute core uses ``@dataclass(slots=True)`` and ``zip(..., strict=True)``,
#: both introduced in Python 3.10. QGIS bundles this interpreter, so this maps
#: to a minimum QGIS of ~3.34 (which ships Python 3.12); older builds such as
#: 3.22/3.28 on Windows still ship Python 3.9.
_MIN_PYTHON = (3, 10)


def classFactory(iface):  # noqa: N802 - name mandated by the QGIS plugin API
    """Construct and return the plugin instance.

    This is the entry point QGIS calls when the plugin is loaded.

    Args:
        iface: The :class:`qgis.gui.QgisInterface` instance QGIS passes to
            every plugin.

    Returns:
        A :class:`bal_toolbox_qgis.plugin.BalToolboxPlugin`.

    Raises:
        RuntimeError: If QGIS is running on Python older than 3.10, with a
            clear message instead of a cryptic ``TypeError`` from the core.
    """
    if sys.version_info < _MIN_PYTHON:
        have = ".".join(str(v) for v in sys.version_info[:3])
        need = ".".join(str(v) for v in _MIN_PYTHON)
        raise RuntimeError(
            f"BAL Toolbox requires Python {need}+ but this QGIS is running "
            f"Python {have}. Please use QGIS 3.34 LTR or newer (which bundles "
            "a compatible Python)."
        )

    from bal_toolbox_qgis.plugin import BalToolboxPlugin

    return BalToolboxPlugin(iface)
