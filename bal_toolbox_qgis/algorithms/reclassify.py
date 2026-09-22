"""Processing algorithm: reclassify a vegetation raster to AS 3959 classes.

A standalone helper that turns a raw vegetation raster into the eight
AS 3959:2018 vegetation classes (1-8) using either a custom remap (an
editable table or a CSV of ``low, high, class`` rows), the built-in NVIS
Major Vegetation Group preset, or a passthrough for rasters that already
carry classes 1-8. The result is the same reclassified raster the main BAL
tool produces internally, exposed on its own so it can be inspected, edited
or reused. All the reclassification maths lives in the untouched compute
core; this only marshals the QGIS parameters.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterMatrix,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from bal_toolbox_qgis.algorithms._help import HELP_URL
from bal_toolbox_qgis.algorithms._qgis_io import export_raster, raster_source_path
from bal_toolbox_qgis.algorithms._remap_presets import (
    default_presets_path,
    get_preset,
    save_preset,
)
from bal_toolbox_qgis.algorithms._remap_rules import (
    PASSTHROUGH_REMAP,
    REMAP_MATRIX_HEADERS,
    parse_csv,
    resolve_custom_remap,
)
from bal_toolbox_qgis.balcore import nvis, terrain
from bal_toolbox_qgis.balcore.raster import read_raster, write_raster

_RULE_SOURCES = [
    "Custom rules - editable table below (falls back to the CSV)",
    "Custom rules - CSV file below",
    "NVIS Major Vegetation Group preset (nvis_mvg)",
    "Already AS 3959 classes 1-8 (passthrough)",
    "Saved preset - recalled by name below",
]


class ReclassifyVegetationAlgorithm(QgsProcessingAlgorithm):
    """Reclassify a raw vegetation raster into AS 3959:2018 classes 1-8."""

    INPUT = "INPUT"
    RULE_SOURCE = "RULE_SOURCE"
    REMAP_TABLE = "REMAP_TABLE"
    REMAP_CSV = "REMAP_CSV"
    PRESET_NAME = "PRESET_NAME"
    SAVE_AS_PRESET = "SAVE_AS_PRESET"
    OUTPUT = "OUTPUT"

    #: Enum index of the "Saved preset" rule source (see ``_RULE_SOURCES``).
    _SAVED_PRESET_SOURCE = 4

    # -- identity ---------------------------------------------------------
    def name(self) -> str:
        return "reclassifyvegetation"

    def displayName(self) -> str:
        return "Reclassify vegetation (custom remap)"

    def group(self) -> str:
        return "Helpers"

    def groupId(self) -> str:
        return "helpers"

    def createInstance(self) -> ReclassifyVegetationAlgorithm:
        return ReclassifyVegetationAlgorithm()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Reclassifies a raw vegetation raster into the eight AS 3959:2018 "
            "vegetation classes (1-8), writing an integer class raster.\n\n"
            "Rules are ``(low, high, class)`` ranges: a raw value 'v' is "
            "assigned 'class' where 'low <= v <= high' (later rules win on "
            "overlap), and cells matching no rule become nodata (-99). Supply "
            "them by any of:\n\n"
            "- Editable table: type Low / High / Class rows directly (the "
            "class must be a whole number 1-8).\n"
            "- CSV file: a file of 'low, high, class' rows, with an optional "
            "header line. Used when the table is left empty.\n"
            "- NVIS preset: the canonical NVIS Major Vegetation Group mapping "
            "(MVG codes 1-32 to AS 3959 classes).\n"
            "- Passthrough: for a raster that already holds classes 1-8, each "
            "class maps to itself.\n"
            "- Saved preset: recall a set of rules you saved earlier by name.\n\n"
            "Tick 'Save these rules as a preset' with a name to store the "
            "resolved rules for reuse here or in the main BAL tool (which offers "
            "the same 'Saved preset' source). Presets are kept in your QGIS user "
            "profile.\n\n"
            "The resulting class raster can be fed straight back into the main "
            "BAL tool by choosing its 'Already AS 3959 classes 1-8' option."
        )

    # -- parameters -------------------------------------------------------
    def initAlgorithm(self, config=None) -> None:
        self.addParameter(
            QgsProcessingParameterRasterLayer(self.INPUT, "Vegetation raster")
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.RULE_SOURCE,
                "Reclassification rules",
                options=_RULE_SOURCES,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterMatrix(
                self.REMAP_TABLE,
                "Remap rules (Low / High / Class)",
                numberRows=1,
                hasFixedNumberRows=False,
                headers=REMAP_MATRIX_HEADERS,
                defaultValue=[],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.REMAP_CSV,
                "Remap CSV of 'low, high, class' rows",
                extension="csv",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.PRESET_NAME,
                "Preset name (recall when rules source is 'Saved preset'; "
                "also the name used when saving below)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SAVE_AS_PRESET,
                "Save these rules as a preset (under the name above)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT, "Vegetation class raster (AS 3959 classes 1-8)"
            )
        )

    # -- execution --------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback) -> dict:
        veg_layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if veg_layer is None:
            raise QgsProcessingException("A vegetation raster is required.")
        veg_path = Path(raster_source_path(veg_layer))

        remap = self._resolve_rules(parameters, context, feedback)
        feedback.pushInfo(f"Reclassifying {veg_path.name} with {len(remap)} rule(s).")

        try:
            veg, grid = read_raster(veg_path)
        except (FileNotFoundError, ValueError) as error:
            raise QgsProcessingException(str(error)) from error
        classes = terrain.reclassify_vegetation(veg, remap)

        # Write a compact signed-integer class raster (values -99 and 1-8 all
        # fit in int16), then mirror it to the requested destination so the
        # user can keep the temporary-file / named-path / other-format choice.
        work_dir = Path(tempfile.mkdtemp(prefix="baltoolbox_"))
        core_tif = work_dir / "vegetation_class.tif"
        write_raster(core_tif, classes, grid, overwrite=True, dtype=np.int16)
        destination = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        out_path = export_raster(str(core_tif), destination, feedback)
        return {self.OUTPUT: out_path}

    # -- helpers ----------------------------------------------------------
    def _resolve_rules(
        self, parameters, context, feedback
    ) -> tuple[tuple[float, float, int], ...]:
        """Return the remap rules for the selected rule source.

        When 'Save these rules as a preset' is ticked and a preset name is
        given, the resolved rules are also written to the shared preset store
        so they can be recalled here or in the main BAL tool. The saved-preset
        source (index 4) recalls rules by name and never re-saves them.
        """
        source = self.parameterAsEnum(parameters, self.RULE_SOURCE, context)
        matrix_values = self.parameterAsMatrix(parameters, self.REMAP_TABLE, context)
        csv_path = self.parameterAsFile(parameters, self.REMAP_CSV, context)
        preset_name = self.parameterAsString(parameters, self.PRESET_NAME, context)
        save_as_preset = self.parameterAsBool(parameters, self.SAVE_AS_PRESET, context)
        try:
            if source == self._SAVED_PRESET_SOURCE:
                return get_preset(default_presets_path(), preset_name)
            if source == 0:
                rules = resolve_custom_remap(matrix_values, csv_path)
            elif source == 1:
                if not csv_path:
                    raise ValueError(
                        "Choose a CSV file of 'low, high, class' rows, or switch "
                        "the rules source to the editable table."
                    )
                rules = parse_csv(csv_path)
            elif source == 2:
                rules = nvis.get_preset("nvis_mvg")
            else:
                # Passthrough uses no rules; a stray table/CSV would be silently
                # ignored, so guard against it rather than mislead the user.
                if matrix_values or csv_path:
                    raise ValueError(
                        "Passthrough uses no rules; clear the remap table and "
                        "CSV, or choose a custom-rules source instead."
                    )
                rules = PASSTHROUGH_REMAP
            if save_as_preset:
                self._save_preset(preset_name, rules, feedback)
            return rules
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error

    def _save_preset(self, name, rules, feedback) -> None:
        """Save ``rules`` under ``name`` and log the outcome (validates name)."""
        if not (name and name.strip()):
            raise ValueError(
                "Tick 'Save these rules as a preset' only with a preset name; "
                "the name above is blank."
            )
        saved = save_preset(default_presets_path(), name, rules)
        feedback.pushInfo(
            f"Saved {len(saved)} rule(s) as remap preset {name.strip()!r} "
            f"({default_presets_path()})."
        )
