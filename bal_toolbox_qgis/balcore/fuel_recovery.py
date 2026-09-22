"""Post-fire fine-fuel recovery model for risk-context analysis.

This module is part of the toolbox's *analysis* layer and is entirely
separate from the AS 3959 bushfire attack level (BAL) calculation. It
estimates how far a cell's fine-fuel load has recovered since the last
fire, as context for prioritisation and vegetation-management planning.
It never modifies a BAL rating: AS 3959 deliberately assumes mature
steady-state fuel, which is the right design assumption for a long-lived
building regardless of when the area last burnt.

Model
-----
Fine-fuel load accumulates after fire along the Olson (1963) single-
exponential curve toward a steady-state maximum:

.. math::

    W(t) = W_{ss} \\, (1 - e^{-k t})

where ``t`` is years since fire, ``W_ss`` is the steady-state (mature)
fine-fuel load and ``k`` (1/yr) is a per-vegetation-class accumulation
rate constant. The *recovery fraction* reported here is
``W(t)/W_ss = 1 - e^{-k t}``, independent of ``W_ss``, so it expresses
"fraction of mature fuel present" in ``[0, 1]``.

The rate constants :data:`RECOVERY_RATE_BY_CLASS` are modelling
assumptions, not values from AS 3959. They are chosen so each
vegetation type reaches ~95% of its mature fine-fuel load over a
literature-consistent recovery time for south-eastern Australian fuels
(fast for grass, slow for forest); they are collected here so they can
be reviewed and tuned in one place. The steady-state loads themselves
are not needed for the fraction, but the class set is kept aligned with
:data:`bal_toolbox_qgis.balcore.method2_tables.FUEL_BY_CLASS`.
"""

from __future__ import annotations

import logging
from typing import Final

import numpy as np
from numpy.typing import NDArray

from bal_toolbox_qgis.balcore import tables

logger = logging.getLogger(__name__)

#: Approximate years for each vegetation class to reach ~95% of its
#: mature fine-fuel load (3 time constants). Used to derive the Olson
#: rate constant k = 3 / years_to_95pct. These are modelling assumptions
#: for risk context, not AS 3959 values; tune here if better local fuel-
#: accumulation data is available.
YEARS_TO_95PCT_BY_CLASS: Final[dict[int, float]] = {
    tables.VEG_FOREST: 25.0,
    tables.VEG_WOODLAND: 20.0,
    tables.VEG_SHRUBLAND: 15.0,
    tables.VEG_SCRUB: 17.0,
    tables.VEG_MALLEE_MULGA: 20.0,
    tables.VEG_RAINFOREST: 30.0,
    tables.VEG_GRASSLAND: 2.0,
    tables.VEG_TUSSOCK_MOORLAND: 4.0,
}

#: Olson fine-fuel accumulation rate constant k (1/yr) per vegetation
#: class, derived so W(t)/W_ss reaches ~0.95 at the tabulated recovery
#: time (1 - e^-3 ~= 0.95, hence k = 3 / years_to_95pct).
RECOVERY_RATE_BY_CLASS: Final[dict[int, float]] = {
    veg_class: 3.0 / years for veg_class, years in YEARS_TO_95PCT_BY_CLASS.items()
}


def recovery_fraction(veg_class: int, years: float) -> float:
    """Fraction of mature fine-fuel load recovered for one class and age.

    Args:
        veg_class: AS 3959 vegetation class (1-8).
        years: Years since the last fire (>= 0).

    Returns:
        The recovered fraction ``1 - e^{-k t}`` in ``[0, 1]``, or ``0.0``
        for an unknown class or zero years since fire.

    Raises:
        ValueError: If ``years`` is negative.
    """
    if years < 0.0:
        raise ValueError(f"'years' since fire must be >= 0; got {years}.")
    rate = RECOVERY_RATE_BY_CLASS.get(veg_class)
    if rate is None:
        return 0.0
    # At years == 0 this is 1 - e^0 = 0.0, so no special-case is needed.
    return float(1.0 - np.exp(-rate * years))


def recovery_fraction_raster(
    veg_class: NDArray[np.int_],
    years_since_fire: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Per-cell recovered fine-fuel fraction from class and time-since-fire.

    A cell is rated only where it has both a known vegetation class and a
    valid years-since-fire value (i.e. it has burnt). Unburnt cells, or
    cells whose class has no recovery rate, are left as nodata.

    Args:
        veg_class: Reclassified AS 3959 vegetation-class raster (1-8) or
            :data:`bal_toolbox_qgis.balcore.tables.NODATA`.
        years_since_fire: Years-since-fire raster (:data:`tables.NODATA`
            where no fire is recorded), e.g. from
            :func:`bal_toolbox_qgis.balcore.fire_history.years_since_fire`.

    Returns:
        A float raster of recovered fractions in ``[0, 1]``;
        :data:`bal_toolbox_qgis.balcore.tables.NODATA` where the cell is unburnt or its
        class is unknown.

    Raises:
        ValueError: If the two rasters have different shapes.
    """
    if veg_class.shape != years_since_fire.shape:
        raise ValueError(
            "veg_class and years_since_fire rasters must have the same shape; "
            f"got {veg_class.shape} and {years_since_fire.shape}."
        )
    nodata = float(tables.NODATA)
    result = np.full(years_since_fire.shape, nodata, dtype=np.float64)

    burnt = years_since_fire != tables.NODATA
    # Build a per-cell rate from the class, leaving unknown classes at 0.
    rate = np.zeros(veg_class.shape, dtype=np.float64)
    for klass, k in RECOVERY_RATE_BY_CLASS.items():
        rate[veg_class == klass] = k

    valid = burnt & (rate > 0.0)
    ages = np.maximum(0.0, years_since_fire[valid])
    result[valid] = 1.0 - np.exp(-rate[valid] * ages)
    logger.info(
        "Computed fuel-recovery fraction for %d of %d cells "
        "(burnt with a known vegetation class).",
        int(valid.sum()),
        result.size,
    )
    return result
