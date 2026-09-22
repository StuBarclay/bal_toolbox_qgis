"""Processing algorithm: fire-history and fuel-recovery footprint analysis."""

from __future__ import annotations

import tempfile
from pathlib import Path

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)

from bal_toolbox_qgis.algorithms._help import HELP_URL
from bal_toolbox_qgis.algorithms._qgis_io import (
    deliver_geojson_to_sink,
    raster_source_path,
    vector_source_to_geojson,
)
from bal_toolbox_qgis.balcore.history_overlay import analyse_history


class FireHistoryAlgorithm(QgsProcessingAlgorithm):
    """Annotate footprints with past-fire context and fuel recovery."""

    BAL_RASTER = "BAL_RASTER"
    FOOTPRINTS = "FOOTPRINTS"
    FIRE_LAYER = "FIRE_LAYER"
    YEAR_FIELD = "YEAR_FIELD"
    ANALYSIS_YEAR = "ANALYSIS_YEAR"
    VEG_CLASS = "VEG_CLASS"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "firehistory"

    def displayName(self) -> str:
        return "Fire history & fuel recovery"

    def group(self) -> str:
        return "Fire history"

    def groupId(self) -> str:
        return "history"

    def createInstance(self) -> FireHistoryAlgorithm:
        return FireHistoryAlgorithm()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Annotates building/parcel footprints with fire-history context "
            "from a fire-history polygon layer, alongside (never replacing) "
            "any BAL rating.\n\n"
            "Each footprint gains 'year_last_fire', 'times_burnt' and "
            "'years_since_fire'. If a reclassified AS 3959 vegetation-class "
            "raster is supplied (for example the 'vegetation_class.tif' QA "
            "output), a coarse 'fuel_recovery_pct' is also estimated from the "
            "time since the last fire and the vegetation's recovery rate.\n\n"
            "The BAL raster defines the reference grid and supplies the "
            "'bal_max' band used only for the printed summary cross-tab. "
            "Fuel-recovery percentages are indicative, not a fuel-load "
            "measurement."
        )

    def initAlgorithm(self, config=None) -> None:
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.BAL_RASTER, "BAL raster (reference grid)"
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.FOOTPRINTS,
                "Building / parcel footprints",
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.FIRE_LAYER,
                "Fire-history polygons",
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.YEAR_FIELD,
                "Fire-year field (in the fire-history layer)",
                parentLayerParameterName=self.FIRE_LAYER,
                defaultValue="year",
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.ANALYSIS_YEAR,
                "Analysis year (defaults to current year)",
                type=QgsProcessingParameterNumber.Integer,
                optional=True,
                minValue=1900,
                maxValue=2200,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.VEG_CLASS,
                "AS 3959 vegetation-class raster (enables fuel recovery)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Footprints with fire history",
                type=QgsProcessing.TypeVectorPolygon,
            )
        )

    def processAlgorithm(self, parameters, context, feedback) -> dict:
        bal_layer = self.parameterAsRasterLayer(parameters, self.BAL_RASTER, context)
        bal_path = Path(raster_source_path(bal_layer, feedback))
        footprints = self.parameterAsSource(parameters, self.FOOTPRINTS, context)
        fire = self.parameterAsSource(parameters, self.FIRE_LAYER, context)
        if footprints is None or fire is None:
            raise QgsProcessingException(
                "Both a footprint layer and a fire-history layer are required."
            )
        year_field = self.parameterAsString(parameters, self.YEAR_FIELD, context)

        analysis_year: int | None = None
        if parameters.get(self.ANALYSIS_YEAR) not in (None, ""):
            analysis_year = self.parameterAsInt(parameters, self.ANALYSIS_YEAR, context)

        veg_layer = self.parameterAsRasterLayer(parameters, self.VEG_CLASS, context)
        veg_class_path = (
            Path(raster_source_path(veg_layer, feedback))
            if veg_layer is not None
            else None
        )

        with tempfile.TemporaryDirectory(prefix="bal_history_") as tmp:
            tmp_dir = Path(tmp)
            footprints_geojson = tmp_dir / "footprints.geojson"
            fire_geojson = tmp_dir / "fire.geojson"
            vector_source_to_geojson(footprints, footprints_geojson)
            vector_source_to_geojson(fire, fire_geojson)
            out_geojson = tmp_dir / "history.geojson"

            feedback.pushInfo(
                f"Analysing {footprints.featureCount()} footprint(s) against "
                f"{fire.featureCount()} fire polygon(s) on field '{year_field}'"
            )
            try:
                summary = analyse_history(
                    bal_path,
                    footprints_geojson,
                    fire_geojson,
                    out_geojson,
                    year_field=year_field,
                    analysis_year=analysis_year,
                    veg_class_raster=veg_class_path,
                    overwrite=True,
                    output_crs=None,
                )
            except (ValueError, FileNotFoundError, KeyError) as error:
                raise QgsProcessingException(str(error)) from error

            self._report_summary(summary, feedback)
            dest_id = deliver_geojson_to_sink(
                self, parameters, self.OUTPUT, str(out_geojson), context
            )
        return {self.OUTPUT: dest_id}

    @staticmethod
    def _report_summary(summary, feedback) -> None:
        """Print the BAL x time-since-fire cross-tab to the feedback log."""
        if not summary:
            return
        feedback.pushInfo("Footprint counts (BAL x time since last fire):")
        for bal_label, bins in summary.items():
            parts = ", ".join(f"{label}: {count}" for label, count in bins.items())
            feedback.pushInfo(f"  {bal_label}: {parts}")
