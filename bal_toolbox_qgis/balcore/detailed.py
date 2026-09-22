"""AS 3959:2018 Appendix B detailed method (Method 2) radiant-heat engine.

This computes the Bushfire Attack Level from first principles: a head-fire
rate of spread per fuel model, fireline intensity, flame length, then the
radiant heat flux ``q = E * phi * tau`` (Equation B6) reaching a receiver
at a given distance -- flame emissive power ``E`` (Stefan-Boltzmann, B7),
the maximum tilted-flame view factor ``phi`` (B8, maximised over flame
angle per Figure B4), and atmospheric transmissivity ``tau`` (B9). The BAL
band follows from radiant-heat thresholds (Table 3.1 / B11).

All equation references are to AS 3959:2018 Appendix B. Constants and fuel
parameters come from :mod:`bal_toolbox_qgis.balcore.method2_tables`.

The per-cell flux depends only on the vegetation class, the effective
slope band and the horizontal distance, so results are memoised on those
keys and the toolbox's existing eight-direction search geometry
(:mod:`bal_toolbox_qgis.balcore.engine`) is reused to evaluate every cell.
"""

from __future__ import annotations

import math
from functools import cache
from typing import Final

import numpy as np
from numpy.typing import NDArray

from bal_toolbox_qgis.balcore import method2_tables as m2
from bal_toolbox_qgis.balcore import tables

#: Representative downslope degrees for each slope band (the band's steeper,
#: more severe edge -- conservative). Band 1 = flat/upslope (0 deg).
_BAND_DEGREES: Final[dict[int, float]] = {
    tables.SLOPE_FLAT_UPSLOPE: 0.0,
    tables.SLOPE_DOWN_0_5: 5.0,
    tables.SLOPE_DOWN_5_10: 10.0,
    tables.SLOPE_DOWN_10_15: 15.0,
    tables.SLOPE_DOWN_15_20: 20.0,
}


def rate_of_spread(
    model: str, fdi: int, understorey: float, veg_height: float | None
) -> float:
    """Head-fire rate of spread (km/h) for a fuel model (Table B4).

    Args:
        model: Fuel-behaviour model (see :mod:`bal_toolbox_qgis.balcore.method2_tables`).
        fdi: Fire Danger Index (one of :data:`bal_toolbox_qgis.balcore.tables.FDI_VALUES`).
        understorey: Understorey fuel load (t/ha).
        veg_height: Vegetation height (m), required by shrub/mallee models.

    Returns:
        Forward rate of spread in km/h.

    Raises:
        ValueError: If the model is unknown or a required input is missing.
    """
    if model == m2.MODEL_FOREST:
        return float(0.0012 * fdi * understorey)
    if model == m2.MODEL_GRASSLAND:
        gfdi = m2.FDI_TO_GFDI[fdi]
        return float(0.13 * gfdi)
    if model in (m2.MODEL_SHRUB, m2.MODEL_MALLEE):
        if veg_height is None:
            raise ValueError(f"Model {model!r} requires a vegetation height.")
        return float(0.029 * m2.WIND_SPEED_KMH**1.21 * veg_height**0.54)
    if model == m2.MODEL_TUSSOCK:
        u = m2.WIND_SPEED_KMH
        return float(
            0.024
            * u**1.312
            * math.exp(0.0243 * m2.TUSSOCK_MOISTURE_FACTOR)
            * (1.0 - math.exp(-0.116 * m2.TUSSOCK_AGE_YEARS))
        )
    raise ValueError(f"Unknown fuel model {model!r}.")


def slope_corrected_ros(ros: float, slope_deg: float) -> float:
    """Adjust rate of spread for effective slope (Equation B1).

    Args:
        ros: Forward rate of spread (km/h).
        slope_deg: Effective downslope in degrees (>= 0).

    Returns:
        Slope-corrected rate of spread (km/h).
    """
    return float(ros * math.exp(0.069 * slope_deg))


def fireline_intensity(total_fuel: float, ros_slope: float) -> float:
    """Fireline intensity I (kW/m), Equation B2.

    Args:
        total_fuel: Total fuel load (t/ha).
        ros_slope: Slope-corrected rate of spread (km/h).

    Returns:
        Fireline intensity in kW/m.
    """
    return float(m2.HEAT_OF_COMBUSTION_KJ_KG * total_fuel * ros_slope / 36.0)


def flame_length(
    model: str, intensity: float, ros_slope: float, total_fuel: float
) -> float:
    """Flame length L_f (m) per fuel model (Equations B3-B5).

    Args:
        model: Fuel-behaviour model.
        intensity: Fireline intensity (kW/m) -- used by B4/B5.
        ros_slope: Slope-corrected rate of spread (km/h) -- used by B3.
        total_fuel: Total fuel load (t/ha) -- used by B3.

    Returns:
        Flame length in metres.
    """
    if model == m2.MODEL_GRASSLAND:
        return float(1.192 * (intensity / 1000.0) ** 0.5)  # B5
    if model in (m2.MODEL_SHRUB, m2.MODEL_MALLEE, m2.MODEL_TUSSOCK):
        return float(0.0775 * intensity**0.46)  # B4
    # Forest / woodland / rainforest (B3): the sustained flame length, which
    # is the standard forest flame-length equation halved.
    return float((13.0 * ros_slope + 0.24 * total_fuel) / 2.0)  # B3


def flame_emissive_power() -> float:
    """Flame emissive power E (kW/m^2), Stefan-Boltzmann (Equation B7)."""
    return float(
        m2.FLAME_EMISSIVITY * m2.STEFAN_BOLTZMANN_KW * m2.FLAME_TEMPERATURE_K**4
    )


def _view_factor(
    flame_len: float,
    distance: float,
    angle_deg: float,
    slope_theta_deg: float,
    h: float,
) -> float:
    """Tilted-flame configuration (view) factor (Equation B8).

    Args:
        flame_len: Flame length L_f (m).
        distance: Plan distance d between site and vegetation (m).
        angle_deg: Flame angle alpha (degrees from vertical).
        slope_theta_deg: Slope of land between site and vegetation (deg).
        h: Elevation of receiver (m).

    Returns:
        The view factor in [0, 1].
    """
    alpha = math.radians(angle_deg)
    theta = math.radians(slope_theta_deg)
    denom = distance - 0.5 * flame_len * math.cos(alpha)
    if denom <= 0.0:
        return 1.0  # receiver within half a (projected) flame length

    x1 = (
        flame_len * math.sin(alpha)
        - 0.5 * flame_len * math.cos(alpha) * math.tan(theta)
        - distance * math.tan(theta)
        - h
    ) / denom
    x2 = (h + denom * math.tan(theta)) / denom
    y = 0.5 * m2.FLAME_WIDTH_M / denom

    def _term(x: float, yy: float) -> float:
        a = x / math.sqrt(1.0 + x * x)
        b = math.atan(yy / math.sqrt(1.0 + x * x))
        c = yy / math.sqrt(1.0 + yy * yy)
        d = math.atan(x / math.sqrt(1.0 + yy * yy))
        return a * b + c * d

    phi = (_term(x1, y) + _term(x2, y)) / math.pi
    return float(min(max(phi, 0.0), 1.0))


def receiver_elevation(
    flame_len: float, distance: float, angle_deg: float, slope_theta_deg: float
) -> float:
    """Auto receiver elevation per AS 3959 Step B9(b)/(c).

    The flame-centre height above the site is ``0.5*Lf*sin(alpha) -
    d*tan(theta)``. If that is at or below ground level the receiver is
    taken at ground level (B9(b), h = 0); otherwise it is taken at the
    flame-centre level (B9(c)).

    Args:
        flame_len: Flame length L_f (m).
        distance: Plan distance d (m).
        angle_deg: Flame angle alpha (degrees).
        slope_theta_deg: Slope between site and vegetation (deg).

    Returns:
        The receiver elevation h (m), >= 0.
    """
    alpha = math.radians(angle_deg)
    theta = math.radians(slope_theta_deg)
    flame_centre = 0.5 * flame_len * math.sin(alpha) - distance * math.tan(theta)
    return float(max(0.0, flame_centre))


def max_view_factor_and_angle(
    flame_len: float,
    distance: float,
    slope_theta_deg: float,
    h: float | None = None,
    err_deg: float = 1.0,
    initial_step_deg: float = 10.0,
) -> tuple[float, float]:
    """Maximum view factor over flame angle (AS 3959 Figure B4 algorithm).

    The view factor varies with flame angle and reaches a maximum for an
    angle between the minimum and maximum; AS 3959 uses that maximum.
    This follows the Figure B4 flow diagram: start the flame angle at the
    slope ``theta``, step upward by ``Delta`` while the view factor keeps
    rising, and when a local maximum is bracketed refine by shrinking
    ``Delta`` tenfold until it is within the calculation error.

    Args:
        flame_len: Flame length L_f (m).
        distance: Plan distance d (m).
        slope_theta_deg: Slope between site and vegetation (deg); also the
            starting flame angle ``alpha_0``.
        h: Fixed receiver elevation (m), e.g. a window level. ``None``
            (the default) uses the AS 3959 B9(b)/(c) auto elevation --
            the flame-centre level when above ground -- recomputed per
            candidate flame angle, which gives the maximum-view default.
        err_deg: Flame-angle calculation error (default 1 degree).
        initial_step_deg: Initial flame-angle increment (default 10).

    Returns:
        A tuple of the maximum view factor in [0, 1] and the flame
        angle (degrees) that achieves it.
    """

    def phi(angle_deg: float) -> float:
        receiver = (
            receiver_elevation(flame_len, distance, angle_deg, slope_theta_deg)
            if h is None
            else h
        )
        return _view_factor(flame_len, distance, angle_deg, slope_theta_deg, receiver)

    alpha0 = slope_theta_deg
    phi0 = phi(alpha0)
    delta = initial_step_deg
    alpha1 = alpha0 + delta
    phi1 = phi(alpha1)

    # Iterate until the increment is within the error tolerance, refining
    # the step each time a local maximum (phi1 >= phi0 and phi1 > phi2) is
    # bracketed (Figure B4).
    for _ in range(1000):  # generous guard against non-convergence
        alpha2 = alpha1 + delta
        phi2 = phi(alpha2)
        if phi1 >= phi0 and phi1 > phi2:
            if delta <= err_deg:
                break
            delta /= 10.0
            alpha1 = alpha0 + delta
            phi1 = phi(alpha1)
        else:
            phi0, alpha0 = phi1, alpha1
            phi1, alpha1 = phi2, alpha2
    return float(min(max(phi1, 0.0), 1.0)), float(alpha1)


def max_view_factor(
    flame_len: float,
    distance: float,
    slope_theta_deg: float,
    h: float | None = None,
) -> float:
    """Maximum view factor only (see :func:`max_view_factor_and_angle`)."""
    return max_view_factor_and_angle(flame_len, distance, slope_theta_deg, h)[0]


def _transmissivity_coeff(index: int) -> float:
    """Transmissivity coefficient a_n (Equation B9) for polynomial term n."""
    c1, c2, c3, c4 = m2.TRANSMISSIVITY_COEFFS[index]
    return float(
        c1
        + c2 * m2.AMBIENT_TEMPERATURE_K
        + c3 * m2.FLAME_TEMPERATURE_K
        + c4 * m2.RELATIVE_HUMIDITY
    )


def transmissivity(path_length: float) -> float:
    """Atmospheric transmissivity tau (Equation B9 / B10.4).

    The AS 3959 quartic ``tau = a0 + a1*L + ... + a4*L^4`` is an empirical
    fit (Ref. 16). At the Table B1 reference conditions it crosses zero
    near a path length of ~43 m and is negative beyond that, so a longer
    path implies no radiant transmission. The result is therefore clamped
    to ``[0, 1]``: ``tau`` is 0 once the polynomial goes negative. This is
    the standard's own behaviour, not an extrapolation -- it does mean the
    detailed method assigns no BAL band beyond the ~43 m path-length range
    of the polynomial (see the module docstring and CHANGELOG).

    Args:
        path_length: Path length L (m); 0 yields tau = 1 (B10.4(c)(i)).

    Returns:
        Transmissivity in [0, 1].
    """
    if path_length <= 0.0:
        return 1.0
    tau = sum(_transmissivity_coeff(n) * path_length**n for n in range(5))
    return float(min(max(tau, 0.0), 1.0))


def _quantise_distance(distance_m: float) -> float:
    """Round a distance to the nearest metre for flux memoisation."""
    return round(distance_m)


@cache
def _flux_for(
    veg_class: int,
    slope_band: int,
    distance_m: float,
    fdi: int,
    receiver_h: float | None = None,
) -> float:
    """Radiant heat flux (kW/m^2) at a distance from a vegetation cell.

    Combines the Appendix B steps for one (class, slope band, distance)
    case. Results are memoised because the raster search re-encounters the
    same combinations many times.

    Args:
        veg_class: AS 3959 vegetation class (1-8).
        slope_band: Effective slope band (1-5; band 6 handled upstream).
        distance_m: Horizontal distance to the cell (m).
        fdi: Fire Danger Index.
        receiver_h: Receiver elevation (m); ``None`` selects the AS 3959
            B9(b)/(c) auto elevation (maximum-view default), a numeric value
            (including 0.0) fixes the receiver at that level.

    Returns:
        Radiant heat flux in kW/m^2 (0 if the class carries no fuel).
    """
    fuel = m2.FUEL_BY_CLASS.get(veg_class)
    if fuel is None:
        return 0.0
    model, understorey, total, veg_height = fuel
    slope_deg = _BAND_DEGREES.get(slope_band, 0.0)

    ros = rate_of_spread(model, fdi, understorey, veg_height)
    ros_s = slope_corrected_ros(ros, slope_deg)
    intensity = fireline_intensity(total, ros_s)
    flame_len = flame_length(model, intensity, ros_s, total)
    if flame_len <= 0.0:
        return 0.0

    # AS 3959 develops the prescriptive tables with the site slope equal to
    # the effective slope; do the same here for the land between site and
    # vegetation (Table B1 note on site slope). A receiver_h of ``None``
    # selects the AS 3959 B9(b)/(c) auto elevation (the maximum-view default);
    # a numeric value (including 0.0) fixes the receiver at that level
    # (e.g. a window).
    phi = max_view_factor(flame_len, distance_m, slope_deg, receiver_h)
    if phi <= 0.0:
        return 0.0

    emissive = flame_emissive_power()
    # Path length L (B10.4(a)). NOTE: the Table B5 transmissivity polynomial
    # goes negative beyond a path length of ~43 m, so tau (and hence flux) is
    # zero there by the standard's own formula. This truncates the outer
    # (BAL-19/12.5) bands relative to the prescriptive tables for very long
    # flames (steep-downslope forest) -- a property of the AS 3959 model, see
    # the module docstring / CHANGELOG.
    path = max(0.0, distance_m - 0.5 * flame_len)
    tau = transmissivity(path)
    return emissive * phi * tau


def flux_to_bal(flux_kw: float) -> float:
    """Map a radiant heat flux (kW/m^2) to a BAL output code (Table 3.1)."""
    thresholds = m2.RADIANT_THRESHOLDS_KW
    if flux_kw >= thresholds[3]:  # >= 40 kW/m^2 -> Flame Zone
        return tables.BAL_FZ
    if flux_kw >= thresholds[2]:  # 29-40
        return 40.0
    if flux_kw >= thresholds[1]:  # 19-29
        return 29.0
    if flux_kw >= thresholds[0]:  # 12.5-19
        return 19.0
    if flux_kw > 0.0:
        return 12.5
    return tables.BAL_LOW


def estimate_bal_m2(
    veg: NDArray[np.int_],
    slope_in_aspect: NDArray[np.int_],
    distance_m: float,
    fdi: int,
    receiver_h: float,
) -> NDArray[np.float64]:
    """Estimate Method 2 BAL for every cell at a fixed distance.

    Drop-in counterpart of :func:`bal_toolbox_qgis.balcore.engine.estimate_bal` for the
    detailed method: same array contract, but the band comes from the
    radiant heat flux rather than a distance-table lookup.

    Args:
        veg: Reclassified vegetation classes (1-8) or nodata.
        slope_in_aspect: Slope band for cells facing the search direction,
            the upslope sentinel, or nodata.
        distance_m: Horizontal distance from the point of interest (m).
        fdi: Fire Danger Index.
        receiver_h: Receiver elevation (m); 0 = AS 3959 B9 auto elevation.

    Returns:
        Array of BAL codes with nodata preserved, grass-type vegetation
        dropped beyond 50 m, and steep (>20 deg) downslope forced to FZ.
    """
    nodata = tables.NODATA
    result = np.full(veg.shape, float(nodata), dtype=np.float64)
    valid = (veg != nodata) & (slope_in_aspect != nodata)
    if not valid.any():
        return result

    steep = valid & (slope_in_aspect == tables.SLOPE_DOWN_GT_20)
    result[steep] = tables.BAL_FZ
    cells = valid & ~steep

    effective = slope_in_aspect.copy()
    effective[effective == tables.UPSLOPE_SENTINEL] = tables.SLOPE_FLAT_UPSLOPE

    qdist = float(_quantise_distance(distance_m))
    rows, cols = np.nonzero(cells)
    for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
        flux = _flux_for(int(veg[r, c]), int(effective[r, c]), qdist, fdi, receiver_h)
        result[r, c] = flux_to_bal(flux)

    if distance_m >= tables.GRASS_MAX_DISTANCE_M:
        is_grass = np.isin(veg, tuple(tables.GRASS_TYPE_CLASSES))
        result[is_grass] = float(nodata)
    return result


def _compute_direction_m2(
    direction: str,
    veg: NDArray[np.int_],
    slope_band: NDArray[np.int_],
    aspect_band: NDArray[np.int_],
    pixel_width: float,
    fdi: int,
    receiver_h: float,
) -> NDArray[np.float64]:
    """Maximum Method 2 BAL contribution from one search direction.

    Mirrors :func:`bal_toolbox_qgis.balcore.engine.compute_direction` but evaluates the
    radiant-heat band via :func:`estimate_bal_m2`.

    Args:
        direction: One of :data:`bal_toolbox_qgis.balcore.engine.DIRECTIONS`.
        veg: Reclassified vegetation raster (1-8) or nodata.
        slope_band: Reclassified slope band raster (1-6) or nodata.
        aspect_band: Reclassified aspect raster (1-9) or nodata.
        pixel_width: Cell size (m).
        fdi: Fire Danger Index.
        receiver_h: Elevation of receiver (m).

    Returns:
        Per-cell maximum Method 2 BAL looking in ``direction``.
    """
    from bal_toolbox_qgis.balcore import engine

    aspect_value = engine._DIRECTION_ASPECT[direction]
    step = engine._DIRECTION_STEP[direction]
    diagonal = direction in ("nw", "ne", "se", "sw")
    spacing = pixel_width * (engine._DIAGONAL_FACTOR if diagonal else 1.0)
    step_count = int(np.ceil(engine.MAX_SEARCH_DISTANCE_M / spacing))

    facing = engine._slope_facing_direction(slope_band, aspect_band, aspect_value)
    veg_float = veg.astype(np.float64)
    facing_float = facing.astype(np.float64)

    result = np.full(veg.shape, float(tables.NODATA), dtype=np.float64)
    for s in range(1, step_count + 1):
        distance_m = (s - 0.5) * spacing
        neighbour_veg = engine._shift_toward_poi(veg_float, step, s).astype(np.int_)
        neighbour_slope = engine._shift_toward_poi(facing_float, step, s).astype(
            np.int_
        )
        contribution = estimate_bal_m2(
            neighbour_veg, neighbour_slope, distance_m, fdi, receiver_h
        )
        np.maximum(result, contribution, out=result)
    return result


def compute_bal_method2(
    veg: NDArray[np.int_],
    slope_band: NDArray[np.int_],
    aspect_band: NDArray[np.int_],
    pixel_width: float,
    fdi: int,
    receiver_elevation_m: float = 0.0,
) -> dict[str, NDArray[np.float64]]:
    """Compute per-direction and overall maximum BAL rasters (Method 2).

    Drop-in counterpart of :func:`bal_toolbox_qgis.balcore.engine.compute_bal` using the
    Appendix B radiant-heat-flux model.

    Args:
        veg: Reclassified vegetation raster (1-8) or nodata.
        slope_band: Reclassified slope band raster (1-6) or nodata.
        aspect_band: Reclassified aspect raster (1-9) or nodata.
        pixel_width: Cell size (m).
        fdi: Fire Danger Index (one of :data:`bal_toolbox_qgis.balcore.tables.FDI_VALUES`).
        receiver_elevation_m: Elevation of the receiver above ground (m).

    Returns:
        Mapping of each search direction to its BAL raster, plus ``"max"``.

    Raises:
        ValueError: If Tussock moorland (class 8) appears with FDI != 50.
    """
    from bal_toolbox_qgis.balcore import engine

    engine.validate_pixel_width(pixel_width)
    if fdi != m2.TUSSOCK_FDI and np.any(veg == tables.VEG_TUSSOCK_MOORLAND):
        raise ValueError(
            "Tussock moorland (vegetation class 8) is only modelled for "
            "FDI 50 in AS 3959:2018; reclassify it or run with FDI 50."
        )

    outputs: dict[str, NDArray[np.float64]] = {}
    overall: NDArray[np.float64] | None = None
    for direction in engine.DIRECTIONS:
        band = _compute_direction_m2(
            direction,
            veg,
            slope_band,
            aspect_band,
            pixel_width,
            fdi,
            receiver_elevation_m,
        )
        outputs[direction] = band
        overall = band if overall is None else np.maximum(overall, band)

    assert overall is not None
    outputs["max"] = overall

    # Promote assessable-but-unrated cells to BAL-LOW, as Method 1 does.
    assessable = slope_band != tables.NODATA
    for raster in outputs.values():
        raster[assessable & (raster == tables.NODATA)] = tables.BAL_LOW
    return outputs
