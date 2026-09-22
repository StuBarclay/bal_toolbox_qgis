"""The QGIS Processing provider that registers the BAL algorithms."""

from __future__ import annotations

from pathlib import Path

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from bal_toolbox_qgis.algorithms import _algorithms


class BalProcessingProvider(QgsProcessingProvider):
    """Groups the BAL toolbox algorithms under one Processing provider."""

    def id(self) -> str:
        return "baltoolbox"

    def name(self) -> str:
        return "BAL Toolbox (AS 3959:2018)"

    def longName(self) -> str:
        return "Bushfire Attack Level Toolbox (AS 3959:2018)"

    def icon(self) -> QIcon:
        icon_path = Path(__file__).with_name("icon.svg")
        if icon_path.exists():
            return QIcon(str(icon_path))
        return super().icon()

    def loadAlgorithms(self) -> None:
        for algorithm in _algorithms():
            self.addAlgorithm(algorithm)
