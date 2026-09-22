"""AS 3959:2018 Appendix B (Method 2) input data and constants.

This module encodes the normative inputs for the detailed
radiant-heat-flux method: the per-class fuel loads and vegetation heights
(Table B3), the FDI-to-GFDI mapping for grassland (Table B2), the fixed
modelling constants (Table B1), and the atmospheric-transmissivity
coefficients (Table B5).

The Table B5 coefficients were decoded from the embedded font glyphs of
the published standard, recovering the minus signs and ``x 10^n``
exponents that the PDF's plain text layer drops -- these coefficients are
sign-sensitive, so the decoded values are used rather than the garbled
text.

Fuel-type fire-behaviour model assignments (Table B4):

* Forest model       -- Forest, Woodland, Rainforest
* Shrub and heath    -- Shrubland, Scrub
* Mallee-Heath       -- Mallee/Mulga
* Tussock Moorland   -- Tussock moorland
* McArthur Grassland -- Grassland
"""

from __future__ import annotations

from typing import Final

from bal_toolbox_qgis.balcore import tables

#: Fire-behaviour model identifiers (Table B4).
MODEL_FOREST: Final[str] = "forest"
MODEL_SHRUB: Final[str] = "shrub"
MODEL_MALLEE: Final[str] = "mallee"
MODEL_TUSSOCK: Final[str] = "tussock"
MODEL_GRASSLAND: Final[str] = "grassland"


#: Per-vegetation-class fuel parameters from AS 3959:2018 Table B3, keyed by
#: the toolbox's AS 3959 vegetation class code (see :mod:`bal_toolbox_qgis.balcore.tables`).
#: Each entry is ``(model, understorey_fuel_t_ha, total_fuel_t_ha,
#: veg_height_m)``. ``veg_height`` is ``None`` for forest-model classes (not
#: used by their rate-of-spread equation).
FUEL_BY_CLASS: Final[dict[int, tuple[str, float, float, float | None]]] = {
    tables.VEG_FOREST: (MODEL_FOREST, 25.0, 35.0, None),
    tables.VEG_WOODLAND: (MODEL_FOREST, 15.0, 25.0, None),
    tables.VEG_SHRUBLAND: (MODEL_SHRUB, 15.0, 15.0, 1.5),
    tables.VEG_SCRUB: (MODEL_SHRUB, 25.0, 25.0, 3.0),
    tables.VEG_MALLEE_MULGA: (MODEL_MALLEE, 8.0, 8.0, 3.0),
    tables.VEG_RAINFOREST: (MODEL_FOREST, 10.0, 12.0, None),
    tables.VEG_GRASSLAND: (MODEL_GRASSLAND, 4.5, 4.5, None),
    tables.VEG_TUSSOCK_MOORLAND: (MODEL_TUSSOCK, 17.0, 17.0, None),
}

#: AS 3959:2018 Table B2: FDI -> deemed-equivalent Grassland FDI (GFDI).
FDI_TO_GFDI: Final[dict[int, int]] = {40: 50, 50: 70, 80: 110, 100: 130}

#: Fixed modelling constants (AS 3959:2018 Table B1).
HEAT_OF_COMBUSTION_KJ_KG: Final[float] = 18_600.0
FLAME_TEMPERATURE_K: Final[float] = 1090.0
FLAME_EMISSIVITY: Final[float] = 0.95
FLAME_WIDTH_M: Final[float] = 100.0
AMBIENT_TEMPERATURE_K: Final[float] = 308.0
RELATIVE_HUMIDITY: Final[float] = 0.25
#: Stefan-Boltzmann constant in kW m^-2 K^-4 (Table B1: 5.67 x 10^-11).
STEFAN_BOLTZMANN_KW: Final[float] = 5.67e-11
#: Design 10 m wind speed (km/h) for shrub/heath, mallee and tussock models.
WIND_SPEED_KMH: Final[float] = 45.0
#: Tussock-moorland fuel-age and moisture factor (Table B1).
TUSSOCK_AGE_YEARS: Final[float] = 20.0
TUSSOCK_MOISTURE_FACTOR: Final[float] = 5.0

#: Tussock moorland is only modelled at FDI 50 (as in Method 1).
TUSSOCK_FDI: Final[int] = 50

#: AS 3959:2018 Table B5 transmissivity coefficients, decoded with correct
#: signs and exponents. Index n -> (C1n, C2n, C3n, C4n). Used as
#: ``an = C1n + C2n*Ta + C3n*Tf + C4n*RH`` (Equation B9).
TRANSMISSIVITY_COEFFS: Final[tuple[tuple[float, float, float, float], ...]] = (
    (1.486, -2.003e-3, 4.68e-5, -6.052e-2),
    (1.225e-2, -5.900e-5, 1.66e-6, -1.759e-3),
    (-1.489e-4, -6.893e-7, -1.922e-8, 2.092e-5),
    (8.381e-7, -3.823e-9, 1.0511e-10, -1.166e-7),
    (-1.685e-9, 7.637e-12, -2.085e-13, 2.350e-10),
)

#: Radiant-heat-flux band thresholds (kW/m^2) for the BAL bands (Table 3.1),
#: ascending: BAL-12.5, BAL-19, BAL-29 and BAL-FZ. A site at or above the
#: last value (40) is BAL-FZ; below the first (12.5) is BAL-LOW.
RADIANT_THRESHOLDS_KW: Final[tuple[float, float, float, float]] = (
    12.5,
    19.0,
    29.0,
    40.0,
)
