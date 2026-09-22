"""Unit tests for the BAL output palette data.

``bal_toolbox_qgis.algorithms._bal_style`` keeps its value/label/colour table
(:data:`BAL_CLASSES`) free of any ``qgis`` import, so it can be checked here
against the compute core's canonical BAL codes without a QGIS runtime. The
renderer builder itself (``apply_bal_style``) needs QGIS and is not exercised.
"""

from __future__ import annotations

import re

from bal_toolbox_qgis.algorithms._bal_style import BAL_CLASSES
from bal_toolbox_qgis.balcore import tables

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_values_match_core_bal_codes() -> None:
    values = {value for value, _label, _hex in BAL_CLASSES}
    expected = {tables.BAL_LOW, tables.BAL_FZ, *tables.BAL_BANDS}
    assert values == expected


def test_values_are_ascending_and_unique() -> None:
    values = [value for value, _label, _hex in BAL_CLASSES]
    assert values == sorted(values)
    assert len(values) == len(set(values))


def test_labels_are_nonempty_and_cover_low_and_flame_zone() -> None:
    labels = [label for _value, label, _hex in BAL_CLASSES]
    assert all(label.strip() for label in labels)
    assert "BAL-LOW" in labels
    assert "BAL-FZ" in labels


def test_colours_are_valid_hex() -> None:
    assert all(_HEX.match(hex_color) for _value, _label, hex_color in BAL_CLASSES)
