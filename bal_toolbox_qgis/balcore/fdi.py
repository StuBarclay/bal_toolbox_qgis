"""Fire Danger Index (FDI) determination for AS 3959:2018 Method 1.

Method 1 has separation-distance tables for only four FDI values --
100, 80, 50 and 40 (:data:`bal_toolbox_qgis.balcore.tables.FDI_VALUES`). This module
determines which of those to use, by either of two routes:

* **Location (Table 2.1).** AS 3959:2018 Table 2.1 assigns a design FDI
  per jurisdiction and fire-weather region. The region is selected by an
  explicit key (exact) or, as a fallback, auto-detected from an AOI
  centre coordinate (coarse, conservative -- see
  :func:`detect_region_from_lonlat`).
* **Weather.** A McArthur Forest Fire Danger Index (FFDI, Mark 5) is
  computed from temperature, relative humidity, wind speed and a drought
  factor, then snapped *up* to the nearest tabulated FDI
  (:func:`snap_to_tabulated`). Live observations come from an external
  provider (see :mod:`bal_toolbox_qgis.balcore.weather`).

.. warning::

   The Table 2.1 values are jurisdictional *design* values and the
   fire-weather sub-district boundaries (e.g. NSW Greater Sydney vs
   general; alpine areas) cannot be reliably inferred from coordinates.
   A weather-derived FFDI is an estimate, not a regulated design value.
   Whichever route is used, the resulting FDI should be confirmed with
   the relevant regulatory authority before it is relied upon for a
   construction assessment.
"""

from __future__ import annotations

import math
from typing import Final

from bal_toolbox_qgis.balcore import tables

#: AS 3959:2018 Table 2.1 design FDI by region key.
#: Where a jurisdiction has sub-regions with different values they are keyed
#: separately; a bare state key carries that state's single value or, where
#: the state is split, its most conservative (highest) value.
TABLE_2_1_FDI: Final[dict[str, int]] = {
    "act": 100,
    "nsw_greater_sydney": 100,  # Greater Hunter, Greater Sydney,
    # Illawarra/Shoalhaven, Far South Coast, Southern Ranges districts
    "nsw_alpine": 50,
    "nsw_general": 80,
    "nsw": 100,  # conservative default for NSW (Greater Sydney value)
    "nt": 40,
    "qld": 40,
    "sa": 80,
    "tas": 50,
    "vic_alpine": 50,
    "vic_general": 100,
    "vic": 100,  # conservative default for Victoria
    "wa": 80,
}

#: Coarse state bounding boxes (lon_min, lat_min, lon_max, lat_max) used only
#: to auto-detect a *state* when no region key is given. Deliberately rough;
#: overlaps are resolved by the most conservative FDI. Not a substitute for an
#: explicit region key.
_STATE_BBOXES: Final[dict[str, tuple[float, float, float, float]]] = {
    "wa": (112.0, -35.5, 129.0, -13.5),
    "nt": (129.0, -26.0, 138.0, -10.5),
    "sa": (129.0, -38.5, 141.0, -26.0),
    "qld": (138.0, -29.0, 154.0, -10.5),
    "nsw": (141.0, -37.6, 154.0, -28.0),
    "vic": (140.9, -39.2, 150.0, -34.0),
    "tas": (143.5, -43.7, 148.6, -39.2),
    "act": (148.7, -35.95, 149.4, -35.1),
}


def fdi_for_region(region: str) -> int:
    """Return the AS 3959:2018 Table 2.1 design FDI for a region key.

    Args:
        region: Region key (case-insensitive), e.g. ``"wa"`` or
            ``"nsw_greater_sydney"``. See :data:`TABLE_2_1_FDI`.

    Returns:
        The tabulated FDI for the region.

    Raises:
        ValueError: If ``region`` is not a known key.
    """
    key = region.strip().lower()
    if key not in TABLE_2_1_FDI:
        available = ", ".join(sorted(TABLE_2_1_FDI))
        raise ValueError(
            f"Unknown FDI region {region!r}; expected one of: {available}."
        )
    return TABLE_2_1_FDI[key]


def detect_region_from_lonlat(longitude: float, latitude: float) -> str:
    """Auto-detect a state region key from a longitude/latitude.

    This is a coarse fallback for when no explicit region key is given.
    It matches the point against rough state bounding boxes and, where
    several match, returns the one with the most conservative (highest)
    Table 2.1 FDI. It cannot resolve fire-weather sub-districts, so the
    value should be treated as a conservative starting point only.

    Args:
        longitude: Longitude in degrees east.
        latitude: Latitude in degrees north (negative in Australia).

    Returns:
        The detected state region key (e.g. ``"nsw"``).

    Raises:
        ValueError: If the point is not within any known state box.
    """
    matches = [
        state
        for state, (lon_min, lat_min, lon_max, lat_max) in _STATE_BBOXES.items()
        if lon_min <= longitude <= lon_max and lat_min <= latitude <= lat_max
    ]
    if not matches:
        raise ValueError(
            f"Could not detect an Australian state for ({longitude}, "
            f"{latitude}); set an explicit FDI region instead."
        )
    # Most conservative (highest FDI) wins on overlap.
    return max(matches, key=lambda state: TABLE_2_1_FDI[state])


def snap_to_tabulated(value: float) -> int:
    """Snap a continuous fire-danger value up to a tabulated Method 1 FDI.

    The value is rounded *up* to the nearest of
    :data:`bal_toolbox_qgis.balcore.tables.FDI_VALUES` (40, 50, 80, 100), capped at
    100. Rounding up is conservative -- it never selects a less severe
    rating than the estimate implies.

    Args:
        value: A continuous fire-danger index (e.g. a computed FFDI).

    Returns:
        The smallest tabulated FDI greater than or equal to ``value``
        (100 if the value exceeds 100).

    Raises:
        ValueError: If ``value`` is negative.
    """
    if value < 0:
        raise ValueError(f"Fire-danger value must be non-negative; got {value}.")
    for tabulated in sorted(tables.FDI_VALUES):
        if value <= tabulated:
            return tabulated
    return max(tables.FDI_VALUES)


def mcarthur_ffdi(
    temperature_c: float,
    relative_humidity_pct: float,
    wind_speed_kmh: float,
    drought_factor: float = 10.0,
) -> float:
    """Compute the McArthur Mark 5 Forest Fire Danger Index (FFDI).

    Uses Noble et al. (1980)'s equation for the McArthur Mark 5 meter::

        FFDI = 2 * exp(-0.45 + 0.987 * ln(DF)
                       - 0.0345 * RH + 0.0338 * T + 0.0234 * V)

    where ``DF`` is the drought factor (0-10), ``RH`` relative humidity
    (%), ``T`` temperature (deg C) and ``V`` the 10 m wind speed (km/h).

    Args:
        temperature_c: Air temperature in degrees Celsius.
        relative_humidity_pct: Relative humidity as a percentage (0-100).
        wind_speed_kmh: 10 m wind speed in km/h.
        drought_factor: Drought factor 0-10 (defaults to 10, the driest /
            most conservative value). The drought factor depends on
            soil-moisture deficit (KBDI) and days since rain, which a
            single weather observation does not provide; the default
            errs toward a higher index.

    Returns:
        The computed FFDI (a continuous, non-negative value).

    Raises:
        ValueError: If ``drought_factor`` is outside 0-10 or relative
            humidity is outside 0-100.
    """
    if not 0.0 <= drought_factor <= 10.0:
        raise ValueError(f"Drought factor must be in 0-10; got {drought_factor}.")
    if not 0.0 <= relative_humidity_pct <= 100.0:
        raise ValueError(
            f"Relative humidity must be in 0-100; got {relative_humidity_pct}."
        )
    # Guard the logarithm: a zero drought factor is clamped to a small value.
    safe_df = max(drought_factor, 1e-3)
    exponent = (
        -0.450
        + 0.987 * math.log(safe_df)
        - 0.0345 * relative_humidity_pct
        + 0.0338 * temperature_c
        + 0.0234 * wind_speed_kmh
    )
    return 2.0 * math.exp(exponent)
