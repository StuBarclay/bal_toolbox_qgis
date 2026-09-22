"""Processing algorithm: run a BAL calculation from a YAML config file.

This exposes the full power of the compute core -- weather-derived FDI,
polygon areas of interest, custom vegetation remaps, the national SRTM
source and QA input rasters -- for users who prefer a single reproducible
configuration file over the point-and-click parameters of the main tool.
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputRasterLayer,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
)

from bal_toolbox_qgis.algorithms._help import HELP_URL
from bal_toolbox_qgis.balcore.config import RunConfig, load_config
from bal_toolbox_qgis.balcore.workflow import run as run_workflow


class BalFromConfigAlgorithm(QgsProcessingAlgorithm):
    """Run a BAL calculation described by a YAML configuration file."""

    CONFIG = "CONFIG"
    OUTPUT_DIR = "OUTPUT_DIR"
    OUTPUT_BAL_MAX = "OUTPUT_BAL_MAX"

    def name(self) -> str:
        return "balfromconfig"

    def displayName(self) -> str:
        return "BAL raster (from YAML config)"

    def group(self) -> str:
        return "BAL assessment"

    def groupId(self) -> str:
        return "assessment"

    def createInstance(self) -> BalFromConfigAlgorithm:
        return BalFromConfigAlgorithm()

    def helpUrl(self) -> str:
        return HELP_URL

    def shortHelpString(self) -> str:
        return (
            "Runs a full BAL calculation from a single YAML configuration "
            "file, the reproducible equivalent of the interactive tool.\n\n"
            "The config file specifies the DEM and vegetation rasters, the "
            "output directory, the Fire Danger Index (a direct value, a "
            "Table 2.1 region, or weather-derived), the vegetation remap "
            "(a preset name or explicit rules), an optional area of interest "
            "(bounding box or polygon) and the output CRS. Leave the output "
            "folder empty to use the one named in the config.\n\n"
            "This is the way to reach features the interactive tool omits: a "
            "polygon AOI, weather-derived FDI, or a bespoke vegetation remap. "
            "Not a substitute for a site assessment by a qualified bushfire "
            "practitioner."
        )

    def initAlgorithm(self, config=None) -> None:
        self.addParameter(
            QgsProcessingParameterFile(
                self.CONFIG,
                "BAL run configuration (YAML)",
                extension="yml",
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_DIR,
                "Output folder (overrides the config's output_dir)",
                optional=True,
                createByDefault=False,
            )
        )
        self.addOutput(
            QgsProcessingOutputRasterLayer(self.OUTPUT_BAL_MAX, "Maximum BAL raster")
        )

    def processAlgorithm(self, parameters, context, feedback) -> dict:
        config_path = Path(self.parameterAsFile(parameters, self.CONFIG, context))
        try:
            config = load_config(config_path)
        except (FileNotFoundError, ValueError) as error:
            raise QgsProcessingException(
                f"Could not load configuration {config_path}: {error}"
            ) from error

        override_dir = self.parameterAsString(parameters, self.OUTPUT_DIR, context)
        if override_dir:
            out_dir = Path(override_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            # RunConfig is a frozen dataclass; rebuild it with the new folder.
            config = self._with_output_dir(config, out_dir)

        config.output_dir.mkdir(parents=True, exist_ok=True)
        feedback.pushInfo(
            f"Running BAL Method {config.method} from {config_path.name} "
            f"-> {config.output_dir}"
        )
        try:
            outputs = run_workflow(config, overwrite=True)
        except (ValueError, FileNotFoundError, KeyError, TypeError) as error:
            raise QgsProcessingException(str(error)) from error

        for name, path in outputs.items():
            feedback.pushInfo(f"  wrote {name}: {path}")
        bal_max = str(outputs.get("max", config.output_dir / "bal_max.tif"))
        return {self.OUTPUT_BAL_MAX: bal_max, self.OUTPUT_DIR: str(config.output_dir)}

    @staticmethod
    def _with_output_dir(config: RunConfig, out_dir: Path) -> RunConfig:
        """Return a copy of ``config`` writing to ``out_dir``."""
        from dataclasses import replace

        return replace(config, output_dir=out_dir)
