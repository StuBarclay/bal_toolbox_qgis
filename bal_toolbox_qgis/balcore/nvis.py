"""Canonical NVIS Major Vegetation Group to AS 3959:2018 mapping.

The National Vegetation Information System (NVIS) classifies Australian
native vegetation into 32 *Major Vegetation Groups* (MVGs), each
identified by an integer code (the ``MVG_NUM`` attribute of the NVIS
classification raster). AS 3959:2018 Method 1, by contrast, recognises
eight bushfire-relevant vegetation classes (see
:data:`bal_toolbox_qgis.balcore.tables.VEG_CLASS_NAMES`).

This module encodes the correspondence between the two schemes as a
single authoritative table, :data:`MVG_TO_AS3959`, and exposes it as a
named remap *preset* (``"nvis_mvg"``) that a run configuration can refer
to by name instead of restating the rules inline. It is the typed,
reusable equivalent of the hand-written ``remap`` block that previously
lived only in the example configuration.

Mapping rationale
------------------

* The eucalypt and other tall/open/low *forests* (MVG 2-4) and the
  *unclassified forest* group (MVG 30) map to Forest.
* The woodland and open-woodland groups (MVG 5-13, 31) map to Woodland.
* The mallee groups (MVG 14, 32) map to Mallee/Mulga.
* The shrubland and heathland groups (MVG 15-18) map to Shrubland.
* Rainforest and vine thickets (MVG 1) map to Rainforest.
* Mangroves, unclassified native vegetation and regrowth (MVG 23, 26,
  29) map to Scrub.
* The grass-type groups split per AS 3959:2018: Tussock Grasslands
  (MVG 19) map to Tussock moorland, while Hummock/Other Grasslands and
  Chenopod/Samphire shrublands (MVG 20-22) map to Grassland.
* Non-fuel groups -- inland aquatic (24), cleared/built (25), naturally
  bare (27) and sea/estuaries (28) -- carry no bushfire fuel and are
  deliberately left unmapped, so cells with those codes receive no BAL.

.. warning::

   Tussock moorland (AS 3959:2018 vegetation class 8) is tabulated only
   for FDI 50. Because this preset maps MVG 19 to class 8, a run over a
   raster that contains MVG 19 cells must use ``fdi: 50``; the BAL
   engine raises :class:`ValueError` otherwise. Override MVG 19 with an
   inline rule if you need to assess Tussock Grasslands at another FDI.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from bal_toolbox_qgis.balcore import tables, terrain

#: Human-readable NVIS Major Vegetation Group names, keyed by MVG code.
MVG_NAMES: Final[dict[int, str]] = {
    1: "Rainforests and Vine Thickets",
    2: "Eucalypt Tall Open Forests",
    3: "Eucalypt Open Forests",
    4: "Eucalypt Low Open Forests",
    5: "Eucalypt Woodlands",
    6: "Acacia Forests and Woodlands",
    7: "Callitris Forests and Woodlands",
    8: "Casuarina Forests and Woodlands",
    9: "Melaleuca Forests and Woodlands",
    10: "Other Forests and Woodlands",
    11: "Eucalypt Open Woodlands",
    12: "Tropical Eucalypt Woodlands/Grasslands",
    13: "Acacia Open Woodlands",
    14: "Mallee Woodlands and Shrublands",
    15: "Low Closed Forests and Tall Closed Shrublands",
    16: "Acacia Shrublands",
    17: "Other Shrublands",
    18: "Heathlands",
    19: "Tussock Grasslands",
    20: "Hummock Grasslands",
    21: "Other Grasslands, Herblands, Sedgelands and Rushlands",
    22: "Chenopod Shrublands, Samphire Shrublands and Forblands",
    23: "Mangroves",
    24: "Inland Aquatic - freshwater, salt lakes, lagoons",
    25: "Cleared, non-native vegetation, buildings",
    26: "Unclassified native vegetation",
    27: "Naturally bare - sand, rock, claypan, mudflat",
    28: "Sea and estuaries",
    29: "Regrowth, modified native vegetation",
    30: "Unclassified Forest",
    31: "Other Open Woodlands",
    32: "Mallee Open Woodlands and Sparse Mallee Shrublands",
}

#: Authoritative NVIS MVG code -> AS 3959:2018 vegetation class mapping.
#: MVG codes absent from this table (24, 25, 27, 28) carry no bushfire
#: fuel and are intentionally unmapped.
MVG_TO_AS3959: Final[dict[int, int]] = {
    1: tables.VEG_RAINFOREST,
    2: tables.VEG_FOREST,
    3: tables.VEG_FOREST,
    4: tables.VEG_FOREST,
    5: tables.VEG_WOODLAND,
    6: tables.VEG_WOODLAND,
    7: tables.VEG_WOODLAND,
    8: tables.VEG_WOODLAND,
    9: tables.VEG_WOODLAND,
    10: tables.VEG_WOODLAND,
    11: tables.VEG_WOODLAND,
    12: tables.VEG_WOODLAND,
    13: tables.VEG_WOODLAND,
    14: tables.VEG_MALLEE_MULGA,
    15: tables.VEG_SHRUBLAND,
    16: tables.VEG_SHRUBLAND,
    17: tables.VEG_SHRUBLAND,
    18: tables.VEG_SHRUBLAND,
    19: tables.VEG_TUSSOCK_MOORLAND,
    20: tables.VEG_GRASSLAND,
    21: tables.VEG_GRASSLAND,
    22: tables.VEG_GRASSLAND,
    23: tables.VEG_SCRUB,
    26: tables.VEG_SCRUB,
    29: tables.VEG_SCRUB,
    30: tables.VEG_FOREST,
    31: tables.VEG_WOODLAND,
    32: tables.VEG_MALLEE_MULGA,
}


def _build_remap_rules(
    mapping: dict[int, int],
) -> tuple[tuple[float, float, int], ...]:
    """Build remap rules from an MVG-to-class mapping.

    Adjacent MVG codes that share a destination class are merged into a
    single ``(low, high, class_code)`` range so the resulting rule set
    matches the compact, range-based form used elsewhere in the toolbox.

    Args:
        mapping: NVIS MVG code to AS 3959 vegetation class mapping.

    Returns:
        Range rules sorted by ascending MVG code, suitable for
        :func:`bal_toolbox_qgis.balcore.terrain.reclassify_vegetation`.
    """
    rules: list[tuple[float, float, int]] = []
    for code in sorted(mapping):
        class_code = mapping[code]
        if rules and rules[-1][2] == class_code and rules[-1][1] == code - 1:
            low, _, _ = rules[-1]
            rules[-1] = (low, float(code), class_code)
        else:
            rules.append((float(code), float(code), class_code))
    return tuple(rules)


#: NVIS MVG remap preset as range rules (the named ``"nvis_mvg"`` preset).
NVIS_MVG_REMAP: Final[tuple[tuple[float, float, int], ...]] = _build_remap_rules(
    MVG_TO_AS3959
)

#: Registry of named remap presets selectable from a run configuration.
REMAP_PRESETS: Final[dict[str, tuple[tuple[float, float, int], ...]]] = {
    "nvis_mvg": NVIS_MVG_REMAP,
}


def get_preset(name: str) -> tuple[tuple[float, float, int], ...]:
    """Return the remap rules for a named preset.

    Args:
        name: Preset name (case-insensitive), e.g. ``"nvis_mvg"``.

    Returns:
        The preset's remap rules.

    Raises:
        KeyError: If ``name`` is not a registered preset.
    """
    key = name.strip().lower()
    if key not in REMAP_PRESETS:
        available = ", ".join(sorted(REMAP_PRESETS))
        raise KeyError(
            f"Unknown remap preset {name!r}; available presets: {available}."
        )
    return REMAP_PRESETS[key]


def reclassify_mvg(
    veg: NDArray[np.floating],
    remap: Sequence[tuple[float, float, int]] | None = None,
) -> NDArray[np.int_]:
    """Reclassify an NVIS MVG raster into AS 3959:2018 classes.

    A thin convenience wrapper over
    :func:`bal_toolbox_qgis.balcore.terrain.reclassify_vegetation` that defaults to
    the canonical :data:`NVIS_MVG_REMAP` rules.

    Args:
        veg: Raw NVIS MVG raster (integer ``MVG_NUM`` codes 1-32).
        remap: Optional override rules; defaults to the canonical NVIS
            MVG preset when omitted.

    Returns:
        Integer vegetation-class raster (1-8, :data:`tables.NODATA`
        where the MVG code is unmapped or nodata).
    """
    return terrain.reclassify_vegetation(
        veg, NVIS_MVG_REMAP if remap is None else remap
    )
