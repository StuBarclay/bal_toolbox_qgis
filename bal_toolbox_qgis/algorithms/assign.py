"""Processing algorithm: assign BAL ratings to building/parcel polygons."""

from __future__ import annotations

import tempfile
from pathlib import Path

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
)

from bal_toolbox_qgis.algorithms._help import HELP_URL
from bal_toolbox_qgis.algorithms._qgis_io import (
    deliver_geojson_to_sink,
    raster_source_path,
    vector_source_to_geojson,
)
from bal_toolbox_qgis.balcore.zonal import assign_bal_to_polygons


class AssignBalAlgorithm(QgsProcessingAlgorithm):
    """Sample a BAL raster onto polygon footprints (buildings/parcels)."""

    BAL_RASTER = "BAL_RASTER"
    POLYGONS = "POLYGONS"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "assignbal"

    def displayName(self) -> str:
        return "Assign BAL to buildings / parcels"

    def group(self) -> str:
        return "BAL assessment"

    def groupId(self) -> str:
        return "assessment"

    def createInstance(self) -> AssignBalAlgorithm:
        return AssignBalAlgorithm()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Assigns a Bushfire Attack Level to each polygon footprint by "
            "sampling a BAL raster (for example the 'bal_max.tif' produced by "
            "the BAL raster tool) under each polygon.\n\n"
            "Two attributes are added to every feature: 'bal_max' (the worst "
            "BAL touching the footprint) and 'bal_dominant' (the most common "
            "BAL value within it). Footprints that fall entirely outside the "
            "BAL grid receive null values. Footprints are reprojected to the "
            "raster's CRS for sampling, so their own CRS need not match.\n\n"
            "Reflects the raster's rating only; it does not perform a "
            "site-specific assessment."
        )

    def initAlgorithm(self, config=None) -> None:
        self.addParameter(
            QgsProcessingParameterRasterLayer(self.BAL_RASTER, "BAL raster")
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.POLYGONS,
                "Building / parcel polygons",
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "BAL-rated footprints",
                type=QgsProcessing.TypeVectorPolygon,
            )
        )

    def processAlgorithm(self, parameters, context, feedback) -> dict:
        bal_layer = self.parameterAsRasterLayer(parameters, self.BAL_RASTER, context)
        bal_path = Path(raster_source_path(bal_layer))
        source = self.parameterAsSource(parameters, self.POLYGONS, context)
        if source is None:
            raise QgsProcessingException("A polygon layer is required.")

        with tempfile.TemporaryDirectory(prefix="bal_assign_") as tmp:
            tmp_dir = Path(tmp)
            polygons_geojson = tmp_dir / "polygons.geojson"
            vector_source_to_geojson(source, polygons_geojson)
            out_geojson = tmp_dir / "bal_rated.geojson"

            feedback.pushInfo(
                f"Sampling {bal_path.name} onto {source.featureCount()} footprint(s)"
            )
            try:
                count = assign_bal_to_polygons(
                    bal_path,
                    polygons_geojson,
                    out_geojson,
                    overwrite=True,
                    output_crs=None,
                )
            except (ValueError, FileNotFoundError, KeyError) as error:
                raise QgsProcessingException(str(error)) from error
            feedback.pushInfo(f"Rated {count} footprint(s).")

            dest_id = deliver_geojson_to_sink(
                self, parameters, self.OUTPUT, str(out_geojson), context
            )
        return {self.OUTPUT: dest_id}
