"""End-to-end smoke test of the vendored pure-Python compute core.

The BAL maths (``engine``, ``terrain``, ``tables``, ``fdi``, ``nvis``)
carries no GDAL/``_rio`` dependency, so it imports and runs anywhere
NumPy is available -- including this sandbox. Beyond checking that the
sed import-rewrite left a runnable package, these tests pin a handful of
AS 3959:2018 table values so a silently mis-wired table would fail.
"""

from __future__ import annotations

import numpy as np
import pytest
from bal_toolbox_qgis.balcore import engine, fdi, nvis, tables, terrain

VALID_BAL_CODES = {
    tables.NODATA,
    tables.BAL_LOW,
    12.5,
    19.0,
    29.0,
    40.0,
    tables.BAL_FZ,
}


def test_estimate_bal_matches_as3959_table_100_forest_flat() -> None:
    # FDI 100, flat/upslope (band 1), Forest (class 1) -> limits (19,25,35,48).
    limit_array = engine.build_limit_array(100)
    veg = np.array([[tables.VEG_FOREST]], dtype=np.int_)
    slope = np.array([[tables.SLOPE_FLAT_UPSLOPE]], dtype=np.int_)

    def bal_at(distance_m: float) -> float:
        return float(engine.estimate_bal(veg, slope, distance_m, limit_array)[0, 0])

    assert bal_at(0.0) == tables.BAL_FZ  # within the flame zone
    assert bal_at(20.0) == 40.0  # >= 19
    assert bal_at(30.0) == 29.0  # >= 25
    assert bal_at(40.0) == 19.0  # >= 35
    assert bal_at(50.0) == 12.5  # >= 48
    assert bal_at(100.0) == 12.5  # capped at the farthest band


def test_estimate_bal_steep_downslope_is_flame_zone() -> None:
    limit_array = engine.build_limit_array(100)
    veg = np.array([[tables.VEG_FOREST]], dtype=np.int_)
    steep = np.array([[tables.SLOPE_DOWN_GT_20]], dtype=np.int_)
    out = engine.estimate_bal(veg, steep, 200.0, limit_array)
    assert float(out[0, 0]) == tables.BAL_FZ


def test_compute_bal_end_to_end_shapes_and_codes() -> None:
    # A small forest grid on flat ground; every direction plus the max.
    shape = (12, 12)
    veg = np.full(shape, tables.VEG_FOREST, dtype=np.int_)
    slope_band = np.full(shape, tables.SLOPE_FLAT_UPSLOPE, dtype=np.int_)
    aspect_band = np.full(shape, 9, dtype=np.int_)  # flat aspect
    outputs = engine.compute_bal(
        veg, slope_band, aspect_band, pixel_width=10.0, fdi=100
    )

    expected_names = set(engine.output_names(write_directions=True))
    assert set(outputs) == expected_names
    assert "max" in outputs
    for name, raster in outputs.items():
        assert raster.shape == shape, name
        present = set(np.unique(raster).tolist())
        assert present <= VALID_BAL_CODES, (name, present - VALID_BAL_CODES)


def test_tussock_moorland_requires_fdi_50() -> None:
    veg = np.full((4, 4), tables.VEG_TUSSOCK_MOORLAND, dtype=np.int_)
    slope_band = np.full((4, 4), tables.SLOPE_FLAT_UPSLOPE, dtype=np.int_)
    aspect_band = np.full((4, 4), 9, dtype=np.int_)
    with pytest.raises(ValueError, match="Tussock moorland"):
        engine.compute_bal(veg, slope_band, aspect_band, pixel_width=10.0, fdi=100)
    # FDI 50 is fine.
    engine.compute_bal(veg, slope_band, aspect_band, pixel_width=10.0, fdi=50)


def test_slope_aspect_of_planar_ramp() -> None:
    # A pure east-west ramp: constant slope, aspect facing west (down-slope
    # toward -x). 10 m cells, 1 m rise per cell -> arctan(0.1) ~= 5.71 deg.
    rows, cols = 5, 6
    elevation = np.tile(np.arange(cols, dtype=np.float64), (rows, 1)) * 1.0
    slope, aspect = terrain.slope_aspect(elevation, pixel_width=10.0, pixel_height=10.0)
    interior = slope[1:-1, 1:-1]
    assert np.allclose(interior, np.degrees(np.arctan(0.1)), atol=1e-6)
    # Elevation increases toward +x (east), so the down-slope aspect faces west.
    assert np.allclose(aspect[1:-1, 1:-1], 270.0, atol=1e-6)


def test_nvis_preset_reclassification() -> None:
    # MVG 2 (Eucalypt Tall Open Forests) -> Forest; MVG 25 (cleared) unmapped.
    mvg = np.array([[2, 5, 25], [14, 19, 1]], dtype=np.float64)
    classes = nvis.reclassify_mvg(mvg)
    assert classes[0, 0] == tables.VEG_FOREST
    assert classes[0, 1] == tables.VEG_WOODLAND
    assert classes[0, 2] == tables.NODATA  # cleared / non-fuel
    assert classes[1, 0] == tables.VEG_MALLEE_MULGA
    assert classes[1, 1] == tables.VEG_TUSSOCK_MOORLAND
    assert classes[1, 2] == tables.VEG_RAINFOREST


def test_fdi_helpers() -> None:
    assert fdi.fdi_for_region("sa") == 80
    assert fdi.fdi_for_region("act") == 100
    # snap_to_tabulated rounds *up* to the next tabulated FDI.
    assert fdi.snap_to_tabulated(63.0) == 80
    assert fdi.snap_to_tabulated(100.0) == 100
    with pytest.raises(ValueError):
        fdi.fdi_for_region("not-a-region")
