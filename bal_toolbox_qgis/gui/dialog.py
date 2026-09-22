"""A friendly dialog for the common BAL Method 1 case.

Gathers a DEM (or the national SRTM DEM fetched over an extent), a
vegetation raster, a Fire Danger Index (an explicit value or an AS 3959
Table 2.1 region), a method and an output folder, then runs the
``baltoolbox:balmethod1`` Processing algorithm and loads the result.
Anything more involved (polygon AOIs, weather FDI, custom vegetation
remaps) is available through the Processing Toolbox or the 'from YAML
config' algorithm.
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsMapLayerProxyModel,
    QgsProcessingFeedback,
    QgsReferencedRectangle,
    QgsSettings,
)
from qgis.gui import (
    QgsExtentWidget,
    QgsFileWidget,
    QgsMapLayerComboBox,
    QgsProjectionSelectionWidget,
)
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

# The dialog passes enum indices straight through to ``balmethod1``, so it
# reuses the algorithm's own option lists to keep the ordering in lock-step.
from bal_toolbox_qgis.algorithms._bal_style import apply_bal_style
from bal_toolbox_qgis.algorithms.bal_run import _FDI_SOURCES, _REGION_KEYS
from bal_toolbox_qgis.balcore import tables

_METHOD_LABELS = [
    "Method 1 (prescriptive tables)",
    "Method 2 (radiant heat — EXPERIMENTAL)",
]

#: Prefix for this dialog's persisted choices in the QGIS user settings.
_SETTINGS_PREFIX = "BalToolbox/dialog/"


class _LogFeedback(QgsProcessingFeedback):
    """Routes Processing feedback into the dialog's log pane."""

    def __init__(self, sink) -> None:
        super().__init__()
        self._sink = sink

    def pushInfo(self, info: str) -> None:
        self._sink(info)

    def reportError(self, error: str, fatalError: bool = False) -> None:
        self._sink(f"ERROR: {error}")

    def pushCommandInfo(self, info: str) -> None:
        self._sink(info)


class BalDialog(QDialog):
    """Simple front-end that delegates to the Processing algorithm."""

    def __init__(self, iface, parent=None) -> None:
        """Build the dialog widgets.

        Args:
            iface: The QGIS interface (used to add result layers).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("BAL Toolbox (AS 3959:2018)")
        self.setMinimumWidth(520)
        self._build_ui()

    # -- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Compute a Bushfire Attack Level raster from a DEM (or the national "
            "SRTM DEM over an extent) and a vegetation raster. For polygon areas "
            "of interest, weather-derived FDI or custom vegetation remaps, use "
            "the Processing Toolbox."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dem_combo.setAllowEmptyLayer(True)
        form.addRow("DEM raster:", self.dem_combo)

        self.national_check = QCheckBox(
            "Use the national SRTM 1-second DEM instead (needs an extent below)"
        )
        self.national_check.toggled.connect(self._on_national_toggled)
        form.addRow("", self.national_check)

        # Rectangular area of interest. Drives the national SRTM DEM fetch and,
        # when the FDI region is left on auto-detect, the Table 2.1 region lookup.
        self.extent_widget = QgsExtentWidget()
        if self.iface is not None:
            self.extent_widget.setMapCanvas(self.iface.mapCanvas())
        form.addRow("Area of interest (extent):", self.extent_widget)

        self.veg_combo = QgsMapLayerComboBox()
        self.veg_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow("Vegetation raster:", self.veg_combo)

        self.fdi_source_combo = QComboBox()
        self.fdi_source_combo.addItems(_FDI_SOURCES)
        self.fdi_source_combo.currentIndexChanged.connect(self._on_fdi_source_changed)
        form.addRow("FDI source:", self.fdi_source_combo)

        self.fdi_combo = QComboBox()
        for value in tables.FDI_VALUES:
            self.fdi_combo.addItem(f"FDI {value}", value)
        form.addRow("FDI value (explicit):", self.fdi_combo)

        self.region_combo = QComboBox()
        self.region_combo.addItems(_REGION_KEYS)
        form.addRow("Region (from location):", self.region_combo)

        self.method_combo = QComboBox()
        self.method_combo.addItems(_METHOD_LABELS)
        form.addRow("Method:", self.method_combo)

        self.directions_check = QCheckBox("Also write the eight directional rasters")
        form.addRow("", self.directions_check)

        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsCoordinateReferenceSystem("EPSG:4283"))
        form.addRow("Output CRS:", self.crs_widget)

        self.output_widget = QgsFileWidget()
        self.output_widget.setStorageMode(QgsFileWidget.GetDirectory)
        form.addRow("Output folder:", self.output_widget)

        layout.addLayout(form)

        # Reflect the initial FDI-source choice (explicit value is active).
        self._on_fdi_source_changed(self.fdi_source_combo.currentIndex())

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Run output appears here…")
        self.log.setMinimumHeight(120)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox()
        # Use fully-scoped enum names (``ButtonRole.AcceptRole`` rather than
        # ``AcceptRole``). PyQt6 -- which QGIS 4 ships -- removed the unscoped
        # aliases for Qt's own enums; the scoped form also works on the PyQt5
        # that QGIS 3.x ships, so this stays compatible with both.
        self.run_button = self.buttons.addButton(
            "Run", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.buttons.accepted.connect(self._on_run)
        self.buttons.rejected.connect(self.close)
        layout.addWidget(self.buttons)

        # Recall the last-used scalar choices, then reflect any dependent
        # widget enable/disable state that the restore may have changed.
        self._restore_settings()
        self._on_national_toggled(self.national_check.isChecked())
        self._on_fdi_source_changed(self.fdi_source_combo.currentIndex())

    # -- settings persistence ---------------------------------------------
    def _restore_settings(self) -> None:
        """Restore the last-used scalar choices from the QGIS user settings.

        Only lightweight, always-valid choices are persisted (booleans, combo
        indices, the output CRS and folder). Map-layer selections are left to
        QGIS, since a stored layer may not exist in a later project. Enum
        indices are clamped to the current option lists so a shorter list in a
        future version can never select an out-of-range item.
        """
        settings = QgsSettings()

        def _index(key: str, combo: QComboBox) -> int:
            stored = settings.value(_SETTINGS_PREFIX + key, 0, type=int)
            # ``combo.count()`` resolves to Any (QGIS/Qt ship no stubs), so wrap
            # the whole expression in ``int`` to keep the declared return type.
            return int(max(0, min(int(stored), combo.count() - 1)))

        self.national_check.setChecked(
            settings.value(_SETTINGS_PREFIX + "use_national", False, type=bool)
        )
        self.fdi_source_combo.setCurrentIndex(
            _index("fdi_source", self.fdi_source_combo)
        )
        self.fdi_combo.setCurrentIndex(_index("fdi_value", self.fdi_combo))
        self.region_combo.setCurrentIndex(_index("region", self.region_combo))
        self.method_combo.setCurrentIndex(_index("method", self.method_combo))
        self.directions_check.setChecked(
            settings.value(_SETTINGS_PREFIX + "write_directions", False, type=bool)
        )
        crs_authid = settings.value(_SETTINGS_PREFIX + "output_crs", "", type=str)
        if crs_authid:
            crs = QgsCoordinateReferenceSystem(crs_authid)
            if crs.isValid():
                self.crs_widget.setCrs(crs)
        output_dir = settings.value(_SETTINGS_PREFIX + "output_dir", "", type=str)
        if output_dir:
            self.output_widget.setFilePath(output_dir)

    def _save_settings(self) -> None:
        """Persist the current scalar choices to the QGIS user settings."""
        settings = QgsSettings()
        settings.setValue(
            _SETTINGS_PREFIX + "use_national", self.national_check.isChecked()
        )
        settings.setValue(
            _SETTINGS_PREFIX + "fdi_source", self.fdi_source_combo.currentIndex()
        )
        settings.setValue(_SETTINGS_PREFIX + "fdi_value", self.fdi_combo.currentIndex())
        settings.setValue(_SETTINGS_PREFIX + "region", self.region_combo.currentIndex())
        settings.setValue(_SETTINGS_PREFIX + "method", self.method_combo.currentIndex())
        settings.setValue(
            _SETTINGS_PREFIX + "write_directions", self.directions_check.isChecked()
        )
        crs = self.crs_widget.crs()
        if crs.isValid():
            settings.setValue(_SETTINGS_PREFIX + "output_crs", crs.authid())
        settings.setValue(
            _SETTINGS_PREFIX + "output_dir", self.output_widget.filePath().strip()
        )

    # -- widget state -----------------------------------------------------
    def _on_national_toggled(self, checked: bool) -> None:
        # The national DEM is fetched over the extent, so the chosen DEM layer
        # is ignored while this is ticked.
        self.dem_combo.setEnabled(not checked)

    def _on_fdi_source_changed(self, index: int) -> None:
        # Index 0 is 'Explicit value'; index 1 is 'From location'.
        explicit = index == 0
        self.fdi_combo.setEnabled(explicit)
        self.region_combo.setEnabled(not explicit)

    # -- helpers ----------------------------------------------------------
    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)
        self.log.repaint()

    # -- run --------------------------------------------------------------
    def _on_run(self) -> None:
        veg_layer = self.veg_combo.currentLayer()
        dem_layer = self.dem_combo.currentLayer()
        output_dir = self.output_widget.filePath().strip()
        use_national = self.national_check.isChecked()
        fdi_source = self.fdi_source_combo.currentIndex()
        region_index = self.region_combo.currentIndex()

        extent = None
        if self.extent_widget.isValid():
            extent = QgsReferencedRectangle(
                self.extent_widget.outputExtent(), self.extent_widget.outputCrs()
            )

        if veg_layer is None:
            self._log("Please choose a vegetation raster.")
            return
        if use_national and extent is None:
            self._log(
                "The national SRTM DEM needs an area of interest — set an extent above."
            )
            return
        if not use_national and dem_layer is None:
            self._log("Please choose a DEM raster, or tick the national SRTM DEM.")
            return
        # 'From location' with the auto-detect region (index 0) needs an extent
        # to work out which AS 3959 Table 2.1 region applies.
        if fdi_source == 1 and region_index == 0 and extent is None:
            self._log(
                "The 'from location' FDI needs either a specific region or an "
                "extent to auto-detect it."
            )
            return
        if not output_dir:
            self._log("Please choose an output folder.")
            return

        # Remember these choices for next time now that they are known good.
        self._save_settings()

        crs = self.crs_widget.crs()
        params = {
            "USE_NATIONAL_DEM": use_national,
            "VEGETATION": veg_layer,
            "REMAP_PRESET": 0,
            "FDI_SOURCE": fdi_source,
            "FDI_VALUE": self.fdi_combo.currentIndex(),
            "REGION": region_index,
            "METHOD": self.method_combo.currentIndex(),
            "RECEIVER_ELEVATION": 0.0,
            "WRITE_DIRECTIONS": self.directions_check.isChecked(),
            "WRITE_INPUTS": False,
            "OUTPUT_CRS": crs
            if crs.isValid()
            else QgsCoordinateReferenceSystem("EPSG:4283"),
            "OUTPUT_DIR": output_dir,
            # Write the primary raster into the chosen folder rather than a
            # temporary file, so the layer we load points at a file the user
            # keeps; the algorithm treats a matching path as a no-op copy.
            "OUTPUT_BAL_MAX": str(Path(output_dir) / "bal_max.tif"),
        }
        # Only pass a DEM layer when we are not fetching the national DEM.
        if not use_national and dem_layer is not None:
            params["DEM"] = dem_layer
        if extent is not None:
            params["EXTENT"] = extent

        # Imported here so the module loads even where the processing plugin
        # is not yet initialised at import time.
        try:
            from qgis import processing
        except ImportError:  # pragma: no cover - environment dependent
            import processing

        self.run_button.setEnabled(False)
        self._log("Running BAL calculation…")
        # Catch broadly on purpose: any failure should be surfaced to the
        # user in the log pane rather than raising into the QGIS UI.
        try:
            results = processing.run(
                "baltoolbox:balmethod1",
                params,
                feedback=_LogFeedback(self._log),
            )
        except Exception as error:
            self._log(f"Run failed: {error}")
            self.run_button.setEnabled(True)
            return

        bal_max = results.get("OUTPUT_BAL_MAX")
        if bal_max:
            layer = self.iface.addRasterLayer(str(bal_max), "BAL max")
            apply_bal_style(layer)
            self._log(f"Done. Loaded {bal_max}")
        else:
            self._log("Done, but no output raster was returned.")
        self.run_button.setEnabled(True)
