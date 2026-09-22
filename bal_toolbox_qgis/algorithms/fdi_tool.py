"""Processing algorithm: determine an AS 3959:2018 Method 1 Fire Danger Index."""

from __future__ import annotations

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
)

from bal_toolbox_qgis.algorithms._help import HELP_URL
from bal_toolbox_qgis.balcore import fdi as fdi_module

_MODES = [
    "From region (AS 3959:2018 Table 2.1)",
    "Auto-detect region from longitude / latitude",
    "From weather observations (McArthur FFDI)",
]
_REGION_KEYS = sorted(fdi_module.TABLE_2_1_FDI)


class FireDangerIndexAlgorithm(QgsProcessingAlgorithm):
    """Determine the Method 1 design FDI (40/50/80/100) for a site."""

    MODE = "MODE"
    REGION = "REGION"
    LONGITUDE = "LONGITUDE"
    LATITUDE = "LATITUDE"
    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    WIND = "WIND"
    DROUGHT = "DROUGHT"
    OUT_FDI = "OUT_FDI"
    OUT_FFDI = "OUT_FFDI"
    OUT_REGION = "OUT_REGION"

    def name(self) -> str:
        return "firedangerindex"

    def displayName(self) -> str:
        return "Fire Danger Index helper"

    def group(self) -> str:
        return "Helpers"

    def groupId(self) -> str:
        return "helpers"

    def createInstance(self) -> FireDangerIndexAlgorithm:
        return FireDangerIndexAlgorithm()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Determines which Method 1 design Fire Danger Index (40, 50, 80 or "
            "100) to use for the main BAL tool, by any of three routes:\n\n"
            "- From region: looks up the AS 3959:2018 Table 2.1 design FDI for "
            "a jurisdiction / fire-weather region key.\n"
            "- Auto-detect region: matches a longitude/latitude to a state "
            "(coarse, conservative) and returns that state's design FDI.\n"
            "- From weather: computes a McArthur Mark 5 Forest Fire Danger "
            "Index from temperature, humidity, wind and drought factor, then "
            "rounds it up to the nearest tabulated FDI.\n\n"
            "Table 2.1 values are jurisdictional design values and weather "
            "estimates are not regulated values; confirm the FDI with the "
            "relevant authority before relying on it."
        )

    def initAlgorithm(self, config=None) -> None:
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE, "Method", options=_MODES, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.REGION,
                "Region (for 'From region')",
                options=_REGION_KEYS,
                defaultValue=_REGION_KEYS.index("sa"),
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LONGITUDE,
                "Longitude (deg E, for auto-detect)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=138.6,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LATITUDE,
                "Latitude (deg N, negative in Australia, for auto-detect)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=-34.9,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TEMPERATURE,
                "Temperature (deg C, for weather)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=40.0,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.HUMIDITY,
                "Relative humidity (%, for weather)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=10.0,
                minValue=0.0,
                maxValue=100.0,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.WIND,
                "Wind speed (km/h, for weather)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=45.0,
                minValue=0.0,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DROUGHT,
                "Drought factor (0-10, for weather)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=10.0,
                minValue=0.0,
                maxValue=10.0,
                optional=True,
            )
        )
        self.addOutput(QgsProcessingOutputNumber(self.OUT_FDI, "Design FDI"))
        self.addOutput(QgsProcessingOutputNumber(self.OUT_FFDI, "Computed FFDI"))
        self.addOutput(QgsProcessingOutputString(self.OUT_REGION, "Region"))

    def processAlgorithm(self, parameters, context, feedback) -> dict:
        mode = self.parameterAsEnum(parameters, self.MODE, context)
        result: dict[str, object] = {
            self.OUT_FDI: None,
            self.OUT_FFDI: None,
            self.OUT_REGION: "",
        }
        try:
            if mode == 0:
                region = _REGION_KEYS[
                    self.parameterAsEnum(parameters, self.REGION, context)
                ]
                fdi = fdi_module.fdi_for_region(region)
                result[self.OUT_REGION] = region
                feedback.pushInfo(f"Table 2.1 design FDI for '{region}': {fdi}")
            elif mode == 1:
                lon = self.parameterAsDouble(parameters, self.LONGITUDE, context)
                lat = self.parameterAsDouble(parameters, self.LATITUDE, context)
                region = fdi_module.detect_region_from_lonlat(lon, lat)
                fdi = fdi_module.fdi_for_region(region)
                result[self.OUT_REGION] = region
                feedback.pushInfo(
                    f"Auto-detected region '{region}' at ({lon}, {lat}); "
                    f"design FDI {fdi}"
                )
            else:
                temp = self.parameterAsDouble(parameters, self.TEMPERATURE, context)
                rh = self.parameterAsDouble(parameters, self.HUMIDITY, context)
                wind = self.parameterAsDouble(parameters, self.WIND, context)
                drought = self.parameterAsDouble(parameters, self.DROUGHT, context)
                ffdi = fdi_module.mcarthur_ffdi(temp, rh, wind, drought)
                fdi = fdi_module.snap_to_tabulated(ffdi)
                result[self.OUT_FFDI] = round(ffdi, 2)
                feedback.pushInfo(
                    f"McArthur FFDI {ffdi:.1f} -> tabulated design FDI {fdi}"
                )
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error

        result[self.OUT_FDI] = int(fdi)
        return result
