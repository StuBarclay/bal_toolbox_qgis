"""QGIS plugin wiring: register the Processing provider and the GUI dialog.

Kept deliberately thin. All BAL computation lives in
:mod:`bal_toolbox_qgis.balcore`; the Processing algorithms live in
:mod:`bal_toolbox_qgis.algorithms`; this module only connects them to the
QGIS application (a Processing provider plus a Plugins-menu action that
opens the friendly dialog).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from bal_toolbox_qgis.provider import BalProcessingProvider

if TYPE_CHECKING:
    from bal_toolbox_qgis.gui.dialog import BalDialog


class BalToolboxPlugin:
    """Top-level plugin object instantiated by ``classFactory``."""

    def __init__(self, iface) -> None:
        """Store the QGIS interface and prepare empty handles.

        Args:
            iface: The ``QgisInterface`` handed to the plugin by QGIS.
        """
        self.iface = iface
        self.provider: BalProcessingProvider | None = None
        self._action: QAction | None = None
        self._dialog: BalDialog | None = None

    # -- lifecycle --------------------------------------------------------
    def initProcessing(self) -> None:
        """Create and register the Processing provider."""
        self.provider = BalProcessingProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self) -> None:
        """Register the provider and add the toolbar/menu action."""
        self.initProcessing()

        icon_path = Path(__file__).with_name("icon.svg")
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._action = QAction(icon, "BAL Toolbox…", self.iface.mainWindow())
        self._action.setObjectName("balToolboxOpenDialog")
        self._action.triggered.connect(self._open_dialog)
        self.iface.addPluginToMenu("&BAL Toolbox", self._action)
        self.iface.addToolBarIcon(self._action)

    def unload(self) -> None:
        """Remove the action and deregister the Processing provider."""
        if self._action is not None:
            self.iface.removePluginMenu("&BAL Toolbox", self._action)
            self.iface.removeToolBarIcon(self._action)
            self._action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    # -- actions ----------------------------------------------------------
    def _open_dialog(self) -> None:
        """Open (creating on first use) the friendly BAL dialog."""
        # Imported lazily so a headless/Processing-only load never needs Qt
        # widgets, and so an import error in the dialog cannot break the
        # Processing provider registration above.
        from bal_toolbox_qgis.gui.dialog import BalDialog

        if self._dialog is None:
            self._dialog = BalDialog(self.iface, self.iface.mainWindow())
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
