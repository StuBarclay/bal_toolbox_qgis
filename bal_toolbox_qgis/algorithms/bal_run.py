"""Processing algorithm: compute BAL (AS 3959:2018 Method 1 or 2) from rasters."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMatrix,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from bal_toolbox_qgis.algorithms._bal_style import apply_bal_style
from bal_toolbox_qgis.algorithms._help import HELP_URL
from bal_toolbox_qgis.algorithms._progress import BalCanceled, direction_progress
from bal_toolbox_qgis.algorithms._qgis_io import export_raster, raster_source_path
from bal_toolbox_qgis.algorithms._remap_presets import default_presets_path, get_preset
from bal_toolbox_qgis.algorithms._remap_rules import (
    PASSTHROUGH_REMAP,
    REMAP_MATRIX_HEADERS,
    resolve_custom_remap,
)
from bal_toolbox_qgis.balcore import fdi as fdi_module
from bal_toolbox_qgis.balcore import nvis, tables
from bal_toolbox_qgis.balcore.config import AOI, FdiSpec, RunConfig
from bal_toolbox_qgis.balcore.workflow import run as run_workflow

# The vegetation-reclassification source: the built-in NVIS preset, custom
# rules typed into a table or read from a CSV, or a passthrough for rasters
# already carrying AS 3959 classes 1-8. Index 0 stays ``nvis_mvg`` so the
# friendly dialog (which always passes ``REMAP_PRESET = 0``) keeps working.
_REMAP_SOURCES = [
    "NVIS Major Vegetation Group preset (nvis_mvg)",
    "Custom rules (table / CSV below)",
    "Already AS 3959 classes 1-8 (passthrough)",
    "Saved preset (named below)",
]
_FDI_SOURCES = ["Explicit value", "From location (AS 3959:2018 Table 2.1)"]
_METHODS = [
    "Method 1 - prescriptive distance tables",
    "Method 2 - radiant heat flux (EXPERIMENTAL)",
]
_REGION_KEYS = ["(auto-detect from area)", *sorted(fdi_module.TABLE_2_1_FDI)]


class BalMethod1Algorithm(QgsProcessingAlgorithm):
    """Compute a Bushfire Attack Level raster from a DEM and vegetation raster."""

    DEM = "DEM"
    USE_NATIONAL_DEM = "USE_NATIONAL_DEM"
    VEGETATION = "VEGETATION"
    REMAP_PRESET = "REMAP_PRESET"
    REMAP_TABLE = "REMAP_TABLE"
    REMAP_CSV = "REMAP_CSV"
    PRESET_NAME = "PRESET_NAME"
    FDI_SOURCE = "FDI_SOURCE"
    FDI_VALUE = "FDI_VALUE"
    REGION = "REGION"
    METHOD = "METHOD"
    RECEIVER_ELEVATION = "RECEIVER_ELEVATION"
    WRITE_DIRECTIONS = "WRITE_DIRECTIONS"
    WRITE_INPUTS = "WRITE_INPUTS"
    EXTENT = "EXTENT"
    OUTPUT_CRS = "OUTPUT_CRS"
    OUTPUT_DIR = "OUTPUT_DIR"
    OUTPUT_BAL_MAX = "OUTPUT_BAL_MAX"

    # -- identity ---------------------------------------------------------
    def name(self) -> str:
        return "balmethod1"

    def displayName(self) -> str:
        return "BAL raster (Method 1 / 2)"

    def group(self) -> str:
        return "BAL assessment"

    def groupId(self) -> str:
        return "assessment"

    def createInstance(self) -> BalMethod1Algorithm:
        return BalMethod1Algorithm()

    def flags(self) -> Any:
        # Declare threading behaviour explicitly. This algorithm is safe to run
        # on a Processing background thread: it only reads/writes rasters through
        # GDAL, fetches the optional national DEM over HTTP and runs pure-NumPy
        # maths -- it never touches the map canvas, ``iface`` or other GUI state
        # from the worker thread. Threading is therefore kept ENABLED (we do not
        # add ``FlagNoThreading``) so the progress bar stays live and the run can
        # be cancelled while the eight-direction search computes.
        return super().flags()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Computes the Bushfire Attack Level (BAL) over an area from a "
            "digital elevation model and a vegetation raster, following the "
            "prescriptive Method 1 separation-distance tables of AS 3959:2018.\n\n"
            "The vegetation raster is reclassified into the eight AS 3959 "
            "vegetation classes. Choose the NVIS Major Vegetation Group "
            "preset, supply custom rules (an editable Low/High/Class table or "
            "a CSV of 'low, high, class' rows -- the table wins when both are "
            "given), or select passthrough when the raster already holds "
            "AS 3959 classes 1-8. Slope, aspect and the directional "
            "distance search are computed in a projected metre CRS; results "
            "are reprojected to the chosen output CRS.\n\n"
            "Leave the DEM empty and tick 'Use national SRTM DEM' to fetch the "
            "1-second national DEM over the area of interest (an extent is then "
            "required). Method 2 (radiant heat flux) is EXPERIMENTAL and not "
            "yet verified against the published tables.\n\n"
            "This tool does not replace a site assessment by a qualified "
            "bushfire practitioner."
        )

    # -- parameters -------------------------------------------------------
    def initAlgorithm(self, config=None) -> None:
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DEM, "Digital elevation model (DEM)", optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.USE_NATIONAL_DEM,
                "Use national SRTM 1-second DEM (fetched over the extent)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(self.VEGETATION, "Vegetation raster")
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.REMAP_PRESET,
                "Vegetation reclassification",
                options=_REMAP_SOURCES,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterMatrix(
                self.REMAP_TABLE,
                "Custom remap rules (used when reclassification is 'Custom')",
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
                "Custom remap CSV of 'low, high, class' rows "
                "(used when the table above is empty)",
                extension="csv",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.PRESET_NAME,
                "Saved remap preset name (used when reclassification is "
                "'Saved preset')",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.FDI_SOURCE,
                "Fire Danger Index source",
                options=_FDI_SOURCES,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.FDI_VALUE,
                "Fire Danger Index value (when source is 'Explicit value')",
                options=[str(v) for v in tables.FDI_VALUES],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.REGION,
                "Region (when source is 'From location')",
                options=_REGION_KEYS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.METHOD, "BAL method", options=_METHODS, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RECEIVER_ELEVATION,
                "Method 2 receiver elevation above ground (m)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.WRITE_DIRECTIONS,
                "Also write the eight directional BAL rasters",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.WRITE_INPUTS,
                "Also write aligned input rasters (DEM, MVG, veg class) for QA",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.EXTENT, "Area of interest (optional)", optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS, "Output CRS", defaultValue="EPSG:4283"
            )
        )
        # The primary result is a single raster, so expose it as an ordinary
        # raster destination: it defaults to "[Save to temporary file]" and only
        # lands on disk permanently when the user names a path.
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_BAL_MAX, "Maximum BAL raster"
            )
        )
        # The optional extras (eight directional rasters, aligned QA inputs) are
        # a set of sibling files, so they need a folder rather than a single
        # destination. Leave it blank unless one of those options is ticked.
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_DIR,
                "Output folder for the directional / QA rasters",
                optional=True,
                createByDefault=False,
            )
        )

    # -- execution --------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback) -> dict:
        use_national = self.parameterAsBool(parameters, self.USE_NATIONAL_DEM, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        veg_layer = self.parameterAsRasterLayer(parameters, self.VEGETATION, context)
        if veg_layer is None:
            raise QgsProcessingException("A vegetation raster is required.")

        remap = self._resolve_remap(parameters, context)

        method = self.parameterAsEnum(parameters, self.METHOD, context) + 1
        receiver = self.parameterAsDouble(parameters, self.RECEIVER_ELEVATION, context)
        write_directions = self.parameterAsBool(
            parameters, self.WRITE_DIRECTIONS, context
        )
        write_inputs = self.parameterAsBool(parameters, self.WRITE_INPUTS, context)

        # Where the core writes its files. If a folder was given, use it -- the
        # directional / QA extras land there too. Otherwise the core writes into
        # a temporary working folder and only the max-BAL raster is exported to
        # the (possibly temporary) raster destination below.
        folder = self.parameterAsString(parameters, self.OUTPUT_DIR, context)
        if folder:
            output_dir = Path(folder)
        elif write_directions or write_inputs:
            raise QgsProcessingException(
                "Set an output folder to keep the directional / QA rasters, or "
                "untick those options."
            )
        else:
            output_dir = Path(tempfile.mkdtemp(prefix="baltoolbox_"))
        output_dir.mkdir(parents=True, exist_ok=True)

        out_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        output_crs = out_crs.authid() if out_crs and out_crs.isValid() else "EPSG:4283"

        aoi = self._build_aoi(parameters, context)
        if use_national and aoi is None:
            raise QgsProcessingException(
                "The national SRTM DEM needs an area of interest (extent) to "
                "bound the area fetched."
            )
        if use_national:
            dem_path = Path("srtm_1s")
        else:
            if dem_layer is None:
                raise QgsProcessingException(
                    "Provide a DEM raster, or tick 'Use national SRTM DEM'."
                )
            dem_path = Path(raster_source_path(dem_layer, feedback))

        fdi_value, fdi_spec = self._build_fdi(parameters, context, aoi)

        config = RunConfig(
            dem_path=dem_path,
            vegetation_path=Path(raster_source_path(veg_layer, feedback)),
            output_dir=output_dir,
            fdi=fdi_value,
            remap=remap,
            write_directions=write_directions,
            aoi=aoi,
            dem_is_national=use_national,
            fdi_spec=fdi_spec,
            write_inputs=write_inputs,
            timestamp=False,
            method=method,
            receiver_elevation_m=receiver,
            output_crs=output_crs,
        )

        feedback.pushInfo(
            f"Running BAL Method {method} (FDI "
            f"{'=' + str(fdi_value) if fdi_value else 'derived'}) -> {output_dir}"
        )
        feedback.setProgress(5)
        if feedback.isCanceled():
            return {}
        # The eight-direction search inside the core reports progress and honours
        # cancellation through ``direction_progress``, which wraps the core's
        # per-direction function for the duration of the run without altering it.
        try:
            with direction_progress(feedback, start=10.0, end=90.0):
                outputs = run_workflow(
                    config, overwrite=True, write_inputs=write_inputs
                )
        except BalCanceled:
            feedback.pushInfo("Run cancelled before completion; no outputs written.")
            return {}
        except (ValueError, FileNotFoundError, KeyError, TypeError) as error:
            raise QgsProcessingException(str(error)) from error
        if feedback.isCanceled():
            return {}
        feedback.setProgress(92)

        for name, path in outputs.items():
            feedback.pushInfo(f"  wrote {name}: {path}")
        core_max = str(outputs.get("max", output_dir / "bal_max.tif"))
        bal_max = export_raster(
            core_max,
            self.parameterAsOutputLayer(parameters, self.OUTPUT_BAL_MAX, context),
            feedback,
        )
        feedback.setProgress(100)
        # Remember the destination so postProcessAlgorithm can style the layer
        # once QGIS has loaded it into the project (GUI / load-on-completion).
        self._bal_max_dest = bal_max
        self._results = {self.OUTPUT_BAL_MAX: bal_max, self.OUTPUT_DIR: str(output_dir)}
        return self._results

    def postProcessAlgorithm(self, context, feedback) -> dict:
        """Apply the standard BAL palette to the loaded max-BAL raster.

        Called by QGIS only when the output is loaded into a project, so the
        result is a styled layer in the interactive/Toolbox path while headless
        runs are unaffected.
        """
        from qgis.core import QgsProcessingUtils

        dest = getattr(self, "_bal_max_dest", None)
        if dest:
            layer = QgsProcessingUtils.mapLayerFromString(dest, context)
            if apply_bal_style(layer):
                feedback.pushInfo("Applied the AS 3959 BAL colour palette.")
        return getattr(self, "_results", {})

    # -- helpers ----------------------------------------------------------
    def _resolve_remap(
        self, parameters, context
    ) -> tuple[tuple[float, float, int], ...]:
        """Return the vegetation remap rules for the selected source.

        Index 0 is the built-in NVIS preset, index 1 the custom rules (an
        editable table, falling back to a CSV), index 2 a passthrough for
        rasters already carrying AS 3959 classes 1-8, and index 3 a saved preset
        recalled by name (see the standalone reclassify helper for saving them).
        """
        source = self.parameterAsEnum(parameters, self.REMAP_PRESET, context)
        if source == 0:
            return nvis.get_preset("nvis_mvg")
        if source == 2:
            return PASSTHROUGH_REMAP
        if source == 3:
            name = self.parameterAsString(parameters, self.PRESET_NAME, context)
            try:
                return get_preset(default_presets_path(), name)
            except ValueError as error:
                raise QgsProcessingException(str(error)) from error
        matrix_values = self.parameterAsMatrix(parameters, self.REMAP_TABLE, context)
        csv_path = self.parameterAsFile(parameters, self.REMAP_CSV, context)
        try:
            return resolve_custom_remap(matrix_values, csv_path)
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error

    def _build_aoi(self, parameters, context) -> AOI | None:
        """Build an EPSG:4326 bbox AOI from the extent parameter, if given."""
        if not self.parameterDefinition(self.EXTENT).checkValueIsAcceptable(
            parameters.get(self.EXTENT), context
        ):
            return None
        rect = self.parameterAsExtent(
            parameters,
            self.EXTENT,
            context,
            QgsCoordinateReferenceSystem("EPSG:4326"),
        )
        if rect.isEmpty():
            return None
        return AOI(
            bbox=(
                rect.xMinimum(),
                rect.yMinimum(),
                rect.xMaximum(),
                rect.yMaximum(),
            ),
            polygon_path=None,
            crs="EPSG:4326",
        )

    def _build_fdi(self, parameters, context, aoi):
        """Return the ``(fdi, fdi_spec)`` pair from the FDI parameters."""
        source = self.parameterAsEnum(parameters, self.FDI_SOURCE, context)
        if source == 0:  # explicit value
            fdi_value = tables.FDI_VALUES[
                self.parameterAsEnum(parameters, self.FDI_VALUE, context)
            ]
            return int(fdi_value), None
        # from location
        region_idx = self.parameterAsEnum(parameters, self.REGION, context)
        region = None if region_idx == 0 else _REGION_KEYS[region_idx]
        if region is None and aoi is None:
            raise QgsProcessingException(
                "The 'From location' FDI needs either a region or an area of "
                "interest (extent) to auto-detect the region."
            )
        return None, FdiSpec(method="location", region=region)
