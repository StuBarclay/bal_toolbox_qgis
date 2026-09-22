"""Unit tests for the shared custom vegetation-remap rule parser.

``bal_toolbox_qgis.algorithms._remap_rules`` deliberately imports nothing
from the ``qgis`` runtime and raises plain :class:`ValueError`, so its
validation can be exercised anywhere Python is available -- as here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bal_toolbox_qgis.algorithms._remap_rules import (
    PASSTHROUGH_REMAP,
    parse_csv,
    parse_matrix,
    resolve_custom_remap,
)
from bal_toolbox_qgis.balcore import tables


def test_passthrough_is_identity_for_all_eight_classes() -> None:
    expected = tuple((float(code), float(code), code) for code in tables.VEG_CLASSES)
    assert expected == PASSTHROUGH_REMAP
    assert {rule[2] for rule in PASSTHROUGH_REMAP} == set(tables.VEG_CLASSES)


def test_parse_matrix_reads_row_major_values() -> None:
    # QGIS delivers a matrix as a flat, row-major list; values may be strings.
    rules = parse_matrix(["0", "10", "1", 10.0, 20.0, 7])
    assert rules == ((0.0, 10.0, 1), (10.0, 20.0, 7))


def test_parse_matrix_empty_is_no_rules() -> None:
    assert parse_matrix([]) == ()
    assert parse_matrix(None) == ()


def test_parse_matrix_skips_blank_trailing_row() -> None:
    rules = parse_matrix(["1", "2", "3", "", "", ""])
    assert rules == ((1.0, 2.0, 3),)


def test_parse_matrix_rejects_ragged_length() -> None:
    with pytest.raises(ValueError, match="three columns"):
        parse_matrix(["1", "2"])


def test_parse_matrix_rejects_class_out_of_range() -> None:
    with pytest.raises(ValueError, match="whole number"):
        parse_matrix(["1", "2", "9"])


def test_parse_matrix_rejects_non_integer_class() -> None:
    with pytest.raises(ValueError, match="whole number"):
        parse_matrix(["1", "2", "3.5"])


def test_parse_matrix_rejects_low_above_high() -> None:
    with pytest.raises(ValueError, match="greater than high"):
        parse_matrix(["20", "10", "1"])


def test_parse_csv_with_header(tmp_path: Path) -> None:
    path = tmp_path / "remap.csv"
    path.write_text("low,high,class\n0,10,1\n10,20,7\n", encoding="utf-8")
    assert parse_csv(str(path)) == ((0.0, 10.0, 1), (10.0, 20.0, 7))


def test_parse_csv_without_header_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "remap.csv"
    path.write_text("1,1,6\n\n2,4,2\n", encoding="utf-8")
    assert parse_csv(str(path)) == ((1.0, 1.0, 6), (2.0, 4.0, 2))


def test_parse_csv_missing_file_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        parse_csv("does-not-exist-remap.csv")


def test_parse_csv_empty_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("low,high,class\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no 'low, high, class' rules"):
        parse_csv(str(path))


def test_parse_csv_malformed_data_row_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("0,10,1\nfoo,bar,baz\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        parse_csv(str(path))


def test_resolve_prefers_matrix_over_csv(tmp_path: Path) -> None:
    path = tmp_path / "remap.csv"
    path.write_text("5,6,2\n", encoding="utf-8")
    rules = resolve_custom_remap(["1", "2", "1"], str(path))
    assert rules == ((1.0, 2.0, 1),)


def test_resolve_falls_back_to_csv_when_matrix_empty(tmp_path: Path) -> None:
    path = tmp_path / "remap.csv"
    path.write_text("5,6,2\n", encoding="utf-8")
    assert resolve_custom_remap([], str(path)) == ((5.0, 6.0, 2),)


def test_resolve_requires_a_source() -> None:
    with pytest.raises(ValueError, match="No custom remap"):
        resolve_custom_remap([], "")
