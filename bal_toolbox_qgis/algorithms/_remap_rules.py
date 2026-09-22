"""Shared parsing of custom vegetation-remap rules for the QGIS glue.

Two entry points on the toolbox let a user supply their own vegetation
reclassification instead of the built-in NVIS preset: an editable table (a
Processing "matrix" parameter) and a CSV file. Both describe the same thing --
a list of ``(low, high, class_code)`` rules where a raw vegetation value ``v``
is assigned ``class_code`` when ``low <= v <= high`` (the rule form consumed by
:func:`bal_toolbox_qgis.balcore.terrain.reclassify_vegetation`).

This module turns either source into the validated tuple form the compute core
expects, applying the same checks as the YAML loader's ``_parse_remap`` (class
in 1-8, ``low <= high``). Keeping it here means the standalone "Reclassify
vegetation" algorithm and the main BAL algorithm share one implementation and
one set of error messages.

Validation failures raise a plain :class:`ValueError` rather than a
``QgsProcessingException`` so this module imports and unit-tests without the
``qgis`` runtime, exactly like the compute core. The calling algorithms wrap
those errors in a ``QgsProcessingException`` for the Processing UI.
"""

from __future__ import annotations

import csv
from pathlib import Path

from bal_toolbox_qgis.balcore import tables

#: Column headers for the editable remap table (a Processing matrix parameter).
REMAP_MATRIX_HEADERS = ["Low", "High", "Class (1-8)"]

#: Identity remap for a raster that already carries AS 3959 classes 1-8: each
#: class maps to itself, so an already-classified raster passes through
#: unchanged while any value outside 1-8 becomes :data:`tables.NODATA`.
PASSTHROUGH_REMAP: tuple[tuple[float, float, int], ...] = tuple(
    (float(code), float(code), code) for code in tables.VEG_CLASSES
)


def _make_rule(
    low: object, high: object, class_code: object, where: str
) -> tuple[float, float, int]:
    """Validate one ``(low, high, class)`` triple, mirroring ``_parse_remap``.

    Args:
        low: Lower bound of the value range (inclusive).
        high: Upper bound of the value range (inclusive).
        class_code: Target AS 3959 vegetation class (a whole number 1-8).
        where: Human-readable location of the rule, for error messages.

    Returns:
        The validated ``(low, high, class_code)`` triple.

    Raises:
        ValueError: If the values are not numbers, the class is
            not a whole number in 1-8, or ``low`` exceeds ``high``.
    """
    try:
        # Values may arrive as numbers or strings (from a matrix editor or
        # CSV); coerce via ``str`` so the ``object`` inputs float cleanly.
        low_f = float(str(low))
        high_f = float(str(high))
        class_f = float(str(class_code))
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"Remap rule {where} must be three numbers 'low, high, class'; "
            f"got ({low!r}, {high!r}, {class_code!r})."
        ) from err
    class_i = int(class_f)
    if class_i != class_f or class_i not in tables.VEG_CLASSES:
        raise ValueError(
            f"Remap class in rule {where} must be a whole number in "
            f"{tables.VEG_CLASSES}; got {class_code!r}."
        )
    if low_f > high_f:
        raise ValueError(
            f"Remap rule {where} has low {low_f} greater than high {high_f}."
        )
    return (low_f, high_f, class_i)


def parse_matrix(values: object) -> tuple[tuple[float, float, int], ...]:
    """Parse the editable remap table into validated rules.

    A Processing matrix parameter with three columns is delivered as a flat,
    row-major list ``[low0, high0, class0, low1, high1, class1, ...]``. Rows
    that are entirely blank (e.g. a trailing empty editor row) are skipped.

    Args:
        values: The matrix value from ``parameterAsMatrix`` (a flat list), or
            ``None``/empty when the table was left blank.

    Returns:
        The remap rules as validated triples (empty when the table is blank).

    Raises:
        ValueError: If the value count is not a multiple of three
            or any populated row is invalid.
    """
    if not values:
        return ()
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            "The remap table could not be read; expected a list of "
            "'low, high, class' values."
        )
    if len(values) % 3 != 0:
        raise ValueError(
            "The remap table must have three columns (Low, High, Class); got "
            f"{len(values)} values, which is not a multiple of three."
        )
    rules: list[tuple[float, float, int]] = []
    for start in range(0, len(values), 3):
        low, high, class_code = values[start], values[start + 1], values[start + 2]
        if all(str(cell).strip() == "" for cell in (low, high, class_code)):
            continue
        rules.append(_make_rule(low, high, class_code, f"row {start // 3 + 1}"))
    return tuple(rules)


def parse_csv(path: str) -> tuple[tuple[float, float, int], ...]:
    """Parse a CSV file of ``low, high, class`` rows into validated rules.

    The file may carry an optional header row (any leading rows whose first
    three cells are not all numeric are treated as a header and skipped), and
    trailing empty cells and blank lines are ignored.

    Args:
        path: Path to the CSV file.

    Returns:
        The remap rules as validated triples.

    Raises:
        ValueError: If the file is missing, a data row is
            malformed, or the file contains no rules.
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise ValueError(f"Remap CSV file not found: {csv_path}.")
    rules: list[tuple[float, float, int]] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for line_no, row in enumerate(csv.reader(handle), start=1):
            cells = [cell.strip() for cell in row]
            while cells and cells[-1] == "":
                cells.pop()
            if not cells:
                continue
            if len(cells) < 3:
                raise ValueError(
                    f"Remap CSV line {line_no} needs three columns "
                    f"(low, high, class); got {row!r}."
                )
            low, high, class_code = cells[0], cells[1], cells[2]
            if not _all_numeric(low, high, class_code):
                # A leading non-numeric row is a header; a later one is an error.
                if not rules:
                    continue
                raise ValueError(
                    f"Remap CSV line {line_no} is not three numbers "
                    f"'low, high, class'; got {row!r}."
                )
            rules.append(_make_rule(low, high, class_code, f"CSV line {line_no}"))
    if not rules:
        raise ValueError(
            f"Remap CSV file {csv_path} contained no 'low, high, class' rules."
        )
    return tuple(rules)


def _all_numeric(*values: str) -> bool:
    """Return whether every value parses as a float."""
    try:
        for value in values:
            float(value)
    except (TypeError, ValueError):
        return False
    return True


def resolve_custom_remap(
    matrix_values: object, csv_path: str
) -> tuple[tuple[float, float, int], ...]:
    """Return custom remap rules, preferring the table over the CSV.

    Args:
        matrix_values: The editable-table value from ``parameterAsMatrix``.
        csv_path: Path to a CSV file of rules, or an empty string.

    Returns:
        The validated remap rules.

    Raises:
        ValueError: If neither source supplies any rule, or a
            supplied source is invalid.
    """
    rules = parse_matrix(matrix_values)
    if rules:
        return rules
    if csv_path:
        return parse_csv(csv_path)
    raise ValueError(
        "No custom remap was provided: fill in the remap table or choose a CSV "
        "file of 'low, high, class' rules."
    )
