"""QGIS Processing algorithms for the BAL toolbox.

Each algorithm is a thin adapter: it collects native QGIS parameters,
materialises any layers into the plain file formats the compute core
expects (GeoTIFF rasters, GeoJSON FeatureCollections), calls into
:mod:`bal_toolbox_qgis.balcore`, and loads the results back as QGIS
layers. All numerical BAL work happens in the untouched core; nothing
here re-implements the standard.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AssignBalAlgorithm",
    "BalFromConfigAlgorithm",
    "BalMethod1Algorithm",
    "FireDangerIndexAlgorithm",
    "FireHistoryAlgorithm",
    "ReclassifyVegetationAlgorithm",
]


def _algorithms() -> list[Any]:
    """Return fresh instances of every algorithm (imported lazily).

    Imported inside the function so that merely importing this package
    does not require the ``qgis`` runtime (keeps the core importable in
    plain Python for testing).
    """
    from bal_toolbox_qgis.algorithms.assign import AssignBalAlgorithm
    from bal_toolbox_qgis.algorithms.bal_run import BalMethod1Algorithm
    from bal_toolbox_qgis.algorithms.bal_run_config import BalFromConfigAlgorithm
    from bal_toolbox_qgis.algorithms.fdi_tool import FireDangerIndexAlgorithm
    from bal_toolbox_qgis.algorithms.history import FireHistoryAlgorithm
    from bal_toolbox_qgis.algorithms.reclassify import ReclassifyVegetationAlgorithm

    return [
        BalMethod1Algorithm(),
        BalFromConfigAlgorithm(),
        AssignBalAlgorithm(),
        FireHistoryAlgorithm(),
        FireDangerIndexAlgorithm(),
        ReclassifyVegetationAlgorithm(),
    ]
