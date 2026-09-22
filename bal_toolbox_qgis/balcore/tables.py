"""AS 3959:2018 Method 1 bushfire attack level (BAL) lookup tables.

This module encodes the prescriptive separation-distance tables from
AS 3959:2018 *Construction of buildings in bushfire-prone areas*,
Tables 2.4 (FDI 100), 2.5 (FDI 80), 2.6 (FDI 50) and 2.7 (FDI 40).

The values were extracted directly from the published standard and
verified cell-by-cell. They supersede the AS 3959:2009 tables used by
the original Geoscience Australia ArcGIS toolbox. The 2018 edition
splits the former combined "Grassland/Tussock moorland" category into
two distinct classes -- Grassland (7) and Tussock moorland (8). Tussock
moorland is tabulated only for FDI 50 (the GFDI 50 grassland case).

Each entry maps a ``(slope_band, vegetation_class)`` key to the four
ascending upper distance limits (metres) that separate the bushfire
attack level bands ``BAL-FZ | BAL-40 | BAL-29 | BAL-19 | BAL-12.5``.
"""

from __future__ import annotations

from typing import Final

#: Recognised Fire Danger Index values (AS 3959:2018 Method 1 tables).
FDI_VALUES: Final[tuple[int, ...]] = (100, 80, 50, 40)

#: Vegetation classification codes used throughout the toolbox.
VEG_FOREST: Final[int] = 1
VEG_WOODLAND: Final[int] = 2
VEG_SHRUBLAND: Final[int] = 3
VEG_SCRUB: Final[int] = 4
VEG_MALLEE_MULGA: Final[int] = 5
VEG_RAINFOREST: Final[int] = 6
VEG_GRASSLAND: Final[int] = 7
VEG_TUSSOCK_MOORLAND: Final[int] = 8

#: Human-readable vegetation class names keyed by class code.
VEG_CLASS_NAMES: Final[dict[int, str]] = {
    1: "Forest",
    2: "Woodland",
    3: "Shrubland",
    4: "Scrub",
    5: "Mallee/Mulga",
    6: "Rainforest",
    7: "Grassland",
    8: "Tussock moorland",
}

#: All vegetation class codes.
VEG_CLASSES: Final[tuple[int, ...]] = tuple(range(1, 9))

#: Grass-type classes assessed only out to 50 m (AS 3959:2018 Cl. 2.2.3.2).
GRASS_TYPE_CLASSES: Final[frozenset[int]] = frozenset({7, 8})

#: Maximum assessment distance for grass-type vegetation (metres).
GRASS_MAX_DISTANCE_M: Final[float] = 50.0

#: Slope band codes. 1 = upslope/flat (0 deg); 2..5 = downslope ranges;
#: 6 = downslope steeper than 20 deg (always BAL-FZ).
SLOPE_FLAT_UPSLOPE: Final[int] = 1
SLOPE_DOWN_0_5: Final[int] = 2
SLOPE_DOWN_5_10: Final[int] = 3
SLOPE_DOWN_10_15: Final[int] = 4
SLOPE_DOWN_15_20: Final[int] = 5
SLOPE_DOWN_GT_20: Final[int] = 6

#: All slope band codes.
SLOPE_BANDS: Final[tuple[int, ...]] = (1, 2, 3, 4, 5, 6)

#: BAL output codes for the five distance classes, closest-to-farthest from
#: the vegetation. Index 0 is the Flame Zone (BAL-FZ); the remaining four are
#: BAL-40, BAL-29, BAL-19 and BAL-12.5. A flame zone forced by a steep
#: (>20 degree) downslope is coded identically as BAL-FZ (see :data:`BAL_FZ`),
#: matching the single BAL-FZ category of AS 3959:2018.
BAL_BANDS: Final[tuple[float, float, float, float, float]] = (
    100.0,
    40.0,
    29.0,
    19.0,
    12.5,
)

#: BAL-FZ (Flame Zone) output code, used for both table-derived flame zone
#: and the steep-downslope (>20 degree) case.
BAL_FZ: Final[float] = 100.0

#: BAL-LOW output code: an assessable site (valid terrain) whose exposure is
#: below BAL-12.5. Distinct from :data:`NODATA`, which marks cells outside the
#: assessed area (no elevation data).
BAL_LOW: Final[float] = 0.0

#: Sentinel slope value meaning "this cell's aspect does not face the search
#: direction", which AS 3959 treats identically to upslope/flat ground.
UPSLOPE_SENTINEL: Final[int] = -1

#: Sentinel nodata value used in raster arrays.
NODATA: Final[int] = -99

#: AS 3959:2018 distance limits keyed by FDI, then (slope_band, veg_class).
#: Each value is the four ascending upper limits (m) for BAL-FZ/40/29/19.
DIST_LIMITS: Final[dict[int, dict[tuple[int, int], tuple[int, int, int, int]]]] = {
    100: {
        (1, 1): (19, 25, 35, 48),
        (1, 2): (12, 16, 24, 33),
        (1, 3): (7, 9, 13, 19),
        (1, 4): (10, 13, 19, 27),
        (1, 5): (6, 8, 12, 17),
        (1, 6): (8, 11, 16, 23),
        (1, 7): (6, 9, 13, 19),
        (2, 1): (24, 32, 43, 57),
        (2, 2): (15, 21, 29, 41),
        (2, 3): (7, 10, 15, 22),
        (2, 4): (11, 15, 22, 31),
        (2, 5): (7, 9, 13, 20),
        (2, 6): (10, 14, 20, 29),
        (2, 7): (7, 10, 15, 22),
        (3, 1): (31, 39, 53, 69),
        (3, 2): (20, 26, 37, 50),
        (3, 3): (8, 11, 17, 25),
        (3, 4): (12, 17, 24, 35),
        (3, 5): (7, 10, 15, 23),
        (3, 6): (13, 18, 26, 36),
        (3, 7): (8, 11, 17, 25),
        (4, 1): (39, 49, 64, 82),
        (4, 2): (25, 33, 45, 60),
        (4, 3): (9, 13, 19, 28),
        (4, 4): (14, 19, 28, 39),
        (4, 5): (8, 11, 18, 26),
        (4, 6): (17, 23, 33, 45),
        (4, 7): (9, 13, 20, 28),
        (5, 1): (50, 61, 78, 98),
        (5, 2): (32, 41, 56, 73),
        (5, 3): (10, 15, 22, 31),
        (5, 4): (15, 21, 31, 43),
        (5, 5): (9, 13, 20, 29),
        (5, 6): (22, 29, 42, 56),
        (5, 7): (11, 15, 23, 32),
    },
    80: {
        (1, 1): (16, 21, 31, 42),
        (1, 2): (10, 14, 20, 29),
        (1, 3): (7, 9, 13, 19),
        (1, 4): (10, 13, 19, 27),
        (1, 5): (6, 8, 12, 17),
        (1, 6): (6, 9, 13, 19),
        (1, 7): (6, 8, 12, 17),
        (2, 1): (20, 27, 37, 50),
        (2, 2): (13, 17, 25, 35),
        (2, 3): (7, 10, 15, 22),
        (2, 4): (11, 15, 22, 31),
        (2, 5): (7, 9, 13, 20),
        (2, 6): (8, 11, 17, 24),
        (2, 7): (7, 9, 14, 20),
        (3, 1): (26, 33, 46, 61),
        (3, 2): (16, 22, 31, 43),
        (3, 3): (8, 11, 17, 25),
        (3, 4): (12, 17, 24, 35),
        (3, 5): (7, 10, 15, 23),
        (3, 6): (11, 15, 22, 31),
        (3, 7): (8, 10, 16, 23),
        (4, 1): (33, 42, 56, 73),
        (4, 2): (21, 28, 39, 53),
        (4, 3): (9, 13, 19, 28),
        (4, 4): (14, 19, 28, 39),
        (4, 5): (8, 11, 18, 26),
        (4, 6): (14, 19, 28, 39),
        (4, 7): (9, 12, 18, 26),
        (5, 1): (42, 52, 68, 87),
        (5, 2): (27, 35, 48, 64),
        (5, 3): (10, 15, 22, 31),
        (5, 4): (15, 21, 31, 43),
        (5, 5): (9, 13, 20, 29),
        (5, 6): (18, 25, 36, 48),
        (5, 7): (10, 14, 21, 30),
    },
    50: {
        (1, 1): (12, 16, 23, 32),
        (1, 2): (7, 10, 15, 22),
        (1, 3): (7, 9, 13, 19),
        (1, 4): (10, 13, 19, 27),
        (1, 5): (6, 8, 12, 17),
        (1, 6): (5, 6, 9, 14),
        (1, 7): (5, 6, 10, 14),
        (1, 8): (7, 9, 14, 20),
        (2, 1): (14, 19, 27, 38),
        (2, 2): (9, 12, 18, 26),
        (2, 3): (7, 10, 15, 22),
        (2, 4): (11, 15, 22, 31),
        (2, 5): (7, 9, 13, 20),
        (2, 6): (6, 8, 12, 17),
        (2, 7): (5, 7, 11, 16),
        (2, 8): (8, 10, 16, 23),
        (3, 1): (18, 24, 34, 46),
        (3, 2): (11, 15, 23, 32),
        (3, 3): (8, 11, 17, 25),
        (3, 4): (12, 17, 24, 35),
        (3, 5): (7, 10, 15, 23),
        (3, 6): (7, 10, 15, 22),
        (3, 7): (6, 8, 13, 19),
        (3, 8): (9, 12, 18, 26),
        (4, 1): (22, 30, 41, 56),
        (4, 2): (14, 19, 28, 40),
        (4, 3): (9, 13, 19, 28),
        (4, 4): (14, 19, 28, 39),
        (4, 5): (8, 11, 18, 26),
        (4, 6): (9, 13, 19, 28),
        (4, 7): (7, 10, 15, 22),
        (4, 8): (10, 13, 20, 29),
        (5, 1): (28, 37, 51, 67),
        (5, 2): (18, 25, 36, 48),
        (5, 3): (10, 15, 22, 31),
        (5, 4): (15, 21, 31, 43),
        (5, 5): (9, 13, 20, 29),
        (5, 6): (12, 17, 25, 35),
        (5, 7): (8, 11, 17, 25),
        (5, 8): (11, 15, 23, 33),
    },
    40: {
        (1, 1): (10, 13, 20, 28),
        (1, 2): (6, 9, 13, 19),
        (1, 3): (7, 9, 13, 19),
        (1, 4): (10, 13, 19, 27),
        (1, 5): (6, 8, 12, 17),
        (1, 6): (4, 5, 8, 12),
        (1, 7): (4, 5, 8, 12),
        (2, 1): (12, 16, 24, 34),
        (2, 2): (8, 11, 16, 23),
        (2, 3): (7, 10, 15, 22),
        (2, 4): (11, 15, 22, 31),
        (2, 5): (7, 9, 13, 20),
        (2, 6): (5, 7, 10, 15),
        (2, 7): (4, 6, 9, 14),
        (3, 1): (15, 20, 29, 41),
        (3, 2): (9, 13, 19, 28),
        (3, 3): (8, 11, 17, 25),
        (3, 4): (12, 17, 24, 35),
        (3, 5): (7, 10, 15, 23),
        (3, 6): (6, 8, 13, 19),
        (3, 7): (5, 7, 11, 16),
        (4, 1): (19, 25, 36, 49),
        (4, 2): (12, 16, 24, 35),
        (4, 3): (9, 13, 19, 28),
        (4, 4): (14, 19, 28, 39),
        (4, 5): (8, 11, 18, 26),
        (4, 6): (8, 11, 16, 24),
        (4, 7): (6, 8, 13, 19),
        (5, 1): (24, 31, 44, 59),
        (5, 2): (15, 21, 31, 42),
        (5, 3): (10, 15, 22, 31),
        (5, 4): (15, 21, 31, 43),
        (5, 5): (9, 13, 20, 29),
        (5, 6): (10, 14, 21, 30),
        (5, 7): (7, 9, 15, 22),
    },
}
