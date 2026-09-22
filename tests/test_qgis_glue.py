"""QGIS-side tests for the Processing glue the plain sandbox cannot import.

Everything here needs a real ``qgis`` (and ``osgeo``) runtime -- the parameter
definitions, the provider wiring, the algorithm ``flags()`` and the raster
styling all touch the QGIS API. The module is therefore skipped where QGIS is
absent (the plain sandbox) and runs in full inside a QGIS Python environment,
e.g. via ``qgis_testrunner`` or ``pytest`` launched from the QGIS console.

Run inside QGIS with, for example::

    QGIS_TEST_MODULE=tests.test_qgis_glue \\
        <qgis>/bin/qgis_process ... qgis_testrunner.py

or simply ``pytest tests/test_qgis_glue.py`` from a shell where ``python`` is
the QGIS-bundled interpreter.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("qgis", reason="QGIS Python runtime required")
pytest.importorskip("osgeo", reason="GDAL Python bindings (osgeo) required")

import numpy as np
from bal_toolbox_qgis.algorithms import _algorithms
from bal_toolbox_qgis.algorithms._bal_style import apply_bal_style
from bal_toolbox_qgis.algorithms._help import HELP_URL
from bal_toolbox_qgis.algorithms._qgis_io import (
    _gdal_openable,
    _materialise_raster,
    raster_source_path,
)
from bal_toolbox_qgis.algorithms.bal_run import BalMethod1Algorithm
from bal_toolbox_qgis.algorithms.reclassify import ReclassifyVegetationAlgorithm
from bal_toolbox_qgis.provider import BalProcessingProvider
from osgeo import gdal, osr
from qgis.core import (
    QgsApplication,
    QgsPalettedRasterRenderer,
    QgsProcessingAlgorithm,
    QgsRasterLayer,
)

#: The algorithm ids that must always be present in the provider.
_EXPECTED_IDS = {"balmethod1", "reclassifyvegetation"}


@pytest.fixture(scope="module", autouse=True)
def qgis_app() -> Iterator[QgsApplication]:
    """Ensure a QgsApplication exists for the module (reusing any running one)."""
    app = QgsApplication.instance()
    created = False
    if app is None:
        app = QgsApplication([], False)
        app.initQgis()
        created = True
    yield app
    if created:
        app.exitQgis()


def _no_threading_flag() -> Any:
    """Return the ``FlagNoThreading`` enum member across PyQt5/PyQt6."""
    flag = getattr(QgsProcessingAlgorithm, "FlagNoThreading", None)
    if flag is None:  # PyQt6 / QGIS 4 scopes the enum
        flag = QgsProcessingAlgorithm.Flag.FlagNoThreading
    return flag


def test_provider_registers_six_algorithms() -> None:
    """The factory yields six algorithms with unique, non-empty ids."""
    algorithms = _algorithms()
    names = [alg.name() for alg in algorithms]
    assert len(algorithms) == 6
    assert all(names)  # no blank ids
    assert len(set(names)) == len(names)  # all unique
    assert _EXPECTED_IDS.issubset(set(names))


def test_provider_id_is_stable() -> None:
    """The provider id used in ``processing.run`` strings stays ``baltoolbox``."""
    assert BalProcessingProvider().id() == "baltoolbox"


def test_create_instance_and_init_for_all() -> None:
    """Each algorithm round-trips through createInstance/initAlgorithm."""
    for alg in _algorithms():
        clone = alg.createInstance()
        assert isinstance(clone, type(alg))
        clone.initAlgorithm()
        assert clone.parameterDefinitions()  # at least one parameter defined


def test_all_algorithms_expose_help_url() -> None:
    """Every algorithm points its Processing "Help" button at the README."""
    for alg in _algorithms():
        assert alg.helpUrl() == HELP_URL


def test_bal_run_exposes_preset_and_remap_params() -> None:
    """The main BAL tool carries the saved-preset and custom-remap parameters."""
    alg = BalMethod1Algorithm()
    alg.initAlgorithm()
    names = {param.name() for param in alg.parameterDefinitions()}
    assert {"PRESET_NAME", "REMAP_TABLE", "REMAP_CSV", "REMAP_PRESET"} <= names


def test_reclassify_exposes_save_preset_params() -> None:
    """The reclassify helper carries both the preset-name and save-toggle."""
    alg = ReclassifyVegetationAlgorithm()
    alg.initAlgorithm()
    names = {param.name() for param in alg.parameterDefinitions()}
    assert {"PRESET_NAME", "SAVE_AS_PRESET"} <= names


def test_bal_run_keeps_threading_enabled() -> None:
    """``flags()`` must NOT set FlagNoThreading (keeps progress/cancel live)."""
    alg = BalMethod1Algorithm()
    assert not bool(alg.flags() & _no_threading_flag())


def test_apply_bal_style_ignores_bad_layers() -> None:
    """Styling tolerates ``None`` and invalid layers without raising."""
    assert apply_bal_style(None) is False
    assert apply_bal_style(QgsRasterLayer("/does/not/exist.tif", "x")) is False


def test_apply_bal_style_sets_paletted_renderer(tmp_path: Path) -> None:
    """A real BAL raster is given a paletted renderer with the BAL classes."""
    path = tmp_path / "bal_max.tif"
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), 4, 4, 1, gdal.GDT_Int16)
    dataset.SetGeoTransform((0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    band = dataset.GetRasterBand(1)
    band.WriteArray(
        np.array(
            [
                [0, 12, 19, 29],
                [40, 100, 0, 19],
                [-99, 29, 40, 100],
                [12, 19, 29, 0],
            ],
            dtype=np.int16,
        )
    )
    band.SetNoDataValue(-99)
    band.FlushCache()
    dataset = None

    layer = QgsRasterLayer(str(path), "bal")
    assert layer.isValid()
    assert apply_bal_style(layer) is True
    assert isinstance(layer.renderer(), QgsPalettedRasterRenderer)


def _write_small_geotiff(path: Path) -> None:
    """Create a tiny valid single-band GeoTIFF at ``path``."""
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), 4, 4, 1, gdal.GDT_Int16)
    dataset.SetGeoTransform((0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(np.ones((4, 4), dtype=np.int16))
    dataset.FlushCache()


def test_gdal_openable_true_for_real_file_false_for_junk(tmp_path: Path) -> None:
    """``_gdal_openable`` accepts a real raster and quietly rejects nonsense."""
    path = tmp_path / "ok.tif"
    _write_small_geotiff(path)
    assert _gdal_openable(str(path)) is True
    assert _gdal_openable(str(tmp_path / "nope.tif")) is False
    assert _gdal_openable("not a raster at all") is False


def test_raster_source_path_returns_plain_file(tmp_path: Path) -> None:
    """A plain file-backed raster is returned unchanged (fast path)."""
    path = tmp_path / "veg.tif"
    _write_small_geotiff(path)
    layer = QgsRasterLayer(str(path), "veg")
    assert layer.isValid()
    returned = raster_source_path(layer)
    assert Path(returned).exists()
    assert _gdal_openable(str(Path(returned)))


def test_raster_source_path_accepts_gdal_subdataset(tmp_path: Path) -> None:
    """A GDAL descriptor (here a subdataset) is wrapped into an on-disk VRT.

    A NetCDF file exposes its variable as a ``NETCDF:"file":var`` subdataset
    descriptor -- the same shape of connection string a File Geodatabase
    raster uses -- which is not a plain file on disk but is GDAL-openable.

    The bare descriptor cannot be handed to the compute core: the core opens
    rasters by path and first checks ``path.exists()``, which a descriptor
    always fails ("Raster not found"). ``raster_source_path`` must therefore
    return a *real, on-disk* path (a VRT wrapping the descriptor), which this
    test asserts explicitly -- the property that regressed in 0.1.7.
    """
    nc_driver = gdal.GetDriverByName("netCDF")
    if nc_driver is None:  # driver not built into this GDAL
        pytest.skip("netCDF driver unavailable")
    src = tmp_path / "grid.nc"
    dataset = nc_driver.Create(str(src), 4, 4, 1, gdal.GDT_Float32)
    dataset.GetRasterBand(1).WriteArray(np.ones((4, 4), dtype=np.float32))
    dataset.FlushCache()
    dataset = None

    descriptor = f'NETCDF:"{src}":Band1'
    assert not Path(descriptor).exists()
    assert _gdal_openable(descriptor)
    layer = QgsRasterLayer(descriptor, "grid", "gdal")
    if not layer.isValid():
        pytest.skip("QGIS could not load the NetCDF subdataset layer")
    returned = raster_source_path(layer)
    # The descriptor must have been wrapped, not returned verbatim.
    assert returned != descriptor
    # The core requires BOTH: the path exists on disk AND GDAL can open it.
    assert Path(returned).exists()
    assert _gdal_openable(str(Path(returned)))
    # And it must read back as a real raster (a lazy VRT over the source).
    reopened = gdal.Open(str(Path(returned)), gdal.GA_ReadOnly)
    assert reopened is not None
    assert reopened.RasterXSize == 4
    assert reopened.RasterYSize == 4
    reopened = None


def test_materialise_raster_writes_geotiff(tmp_path: Path) -> None:
    """The materialisation fallback yields a real, GDAL-openable GeoTIFF."""
    path = tmp_path / "src.tif"
    _write_small_geotiff(path)
    layer = QgsRasterLayer(str(path), "src")
    assert layer.isValid()
    out = _materialise_raster(layer)
    assert out.endswith(".tif")
    assert Path(out).exists()
    assert _gdal_openable(out)
