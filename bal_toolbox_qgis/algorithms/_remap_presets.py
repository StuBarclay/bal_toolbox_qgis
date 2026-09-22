"""A small on-disk store for named custom vegetation-remap presets.

The custom remap on the toolbox lets a user type ``(low, high, class)`` rules
into a table or read them from a CSV. Re-entering the same rules for every run
is tedious, so this module lets those rules be saved under a name and recalled
later. Presets are kept as JSON in the QGIS user profile, so they persist across
sessions and are shared by the main BAL tool and the standalone reclassify
helper.

The persisted form is intentionally simple::

    {"version": 1, "presets": {"my rules": [[0, 9, 1], [10, 19, 3]]}}

Every rule is re-validated with the same checks as the interactive parser
(:func:`bal_toolbox_qgis.algorithms._remap_rules._make_rule`) whenever a preset
is written or read, so a hand-edited or corrupt file cannot smuggle an invalid
class into a run.

Like :mod:`bal_toolbox_qgis.algorithms._remap_rules`, this module performs no
``qgis`` import at module load and raises plain :class:`ValueError` on bad input,
so the CRUD logic unit-tests without a QGIS runtime. Only
:func:`default_presets_path` touches QGIS, and it does so lazily.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from bal_toolbox_qgis.algorithms._remap_rules import _make_rule

#: A validated set of remap rules (``(low, high, class)`` triples).
RemapRules = tuple[tuple[float, float, int], ...]

#: Schema version written into the presets file for forward compatibility.
_SCHEMA_VERSION = 1


def default_presets_path() -> Path:
    """Return the JSON file custom remap presets are stored in.

    Uses the active QGIS user profile directory when QGIS is available (so
    presets live alongside the user's other QGIS settings), falling back to a
    dot-directory in the user's home when it is not (e.g. in tests).

    Returns:
        The path to ``remap_presets.json`` (which may not yet exist).
    """
    try:
        from qgis.core import QgsApplication

        settings_dir = QgsApplication.qgisSettingsDirPath()
    except Exception:  # pragma: no cover - only hit outside QGIS
        settings_dir = ""
    base = Path(settings_dir) if settings_dir else Path.home() / ".bal_toolbox"
    return base / "bal_toolbox" / "remap_presets.json"


def _validate_name(name: str) -> str:
    """Return a cleaned, non-empty preset name or raise ``ValueError``."""
    cleaned = str(name).strip()
    if not cleaned:
        raise ValueError("A preset name is required (it must not be blank).")
    return cleaned


def _validate_rules(rules: Iterable[Iterable[object]], where: str) -> RemapRules:
    """Return ``rules`` as validated triples, mirroring the interactive parser."""
    validated: list[tuple[float, float, int]] = []
    for index, rule in enumerate(rules, start=1):
        values = list(rule)
        if len(values) != 3:
            raise ValueError(
                f"Remap rule {index} in {where} must be 'low, high, class'; "
                f"got {values!r}."
            )
        low, high, class_code = values
        validated.append(_make_rule(low, high, class_code, f"{where} rule {index}"))
    if not validated:
        raise ValueError(f"Cannot use an empty set of remap rules for {where}.")
    return tuple(validated)


def load_presets(path: Path) -> dict[str, RemapRules]:
    """Load and validate every saved preset from ``path``.

    A missing file yields an empty mapping (no presets saved yet). Each preset's
    rules are re-validated on read, so a corrupt or hand-edited entry surfaces as
    a clear error rather than a bad run.

    Args:
        path: The presets JSON file.

    Returns:
        Mapping of preset name to its validated remap rules.

    Raises:
        ValueError: If the file exists but is not readable as the expected
            ``{"presets": {name: rules}}`` structure, or a stored rule is
            invalid.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"Could not read remap presets from {path}: {err}") from err
    if not isinstance(raw, dict) or not isinstance(raw.get("presets"), dict):
        raise ValueError(
            f"Remap presets file {path} is not in the expected format "
            '({"version": 1, "presets": {...}}).'
        )
    presets: dict[str, RemapRules] = {}
    for name, rules in raw["presets"].items():
        if not isinstance(rules, list):
            raise ValueError(
                f"Preset {name!r} in {path} must be a list of 'low, high, class' rules."
            )
        presets[str(name)] = _validate_rules(rules, f"preset {name!r}")
    return presets


def list_preset_names(path: Path) -> list[str]:
    """Return the saved preset names, sorted, or ``[]`` when there are none."""
    return sorted(load_presets(path))


def get_preset(path: Path, name: str) -> RemapRules:
    """Return the validated rules of one saved preset.

    Args:
        path: The presets JSON file.
        name: The preset to fetch.

    Returns:
        The preset's validated remap rules.

    Raises:
        ValueError: If no preset of that name exists (the message lists the
            names that are available).
    """
    wanted = _validate_name(name)
    presets = load_presets(path)
    if wanted not in presets:
        available = ", ".join(sorted(presets)) or "(none saved yet)"
        raise ValueError(
            f"No saved remap preset named {wanted!r}. Available presets: {available}."
        )
    return presets[wanted]


def save_preset(path: Path, name: str, rules: Iterable[Iterable[object]]) -> RemapRules:
    """Validate and save ``rules`` under ``name``, overwriting any existing one.

    Args:
        path: The presets JSON file (created, with parent folders, if needed).
        name: The preset name to save under.
        rules: The remap rules to store (validated before writing).

    Returns:
        The validated rules that were stored.

    Raises:
        ValueError: If the name is blank or the rules are empty/invalid.
    """
    wanted = _validate_name(name)
    validated = _validate_rules(rules, f"preset {wanted!r}")
    presets = load_presets(path)
    presets[wanted] = validated
    _write(path, presets)
    return validated


def delete_preset(path: Path, name: str) -> None:
    """Remove a saved preset.

    Args:
        path: The presets JSON file.
        name: The preset to remove.

    Raises:
        ValueError: If no preset of that name exists.
    """
    wanted = _validate_name(name)
    presets = load_presets(path)
    if wanted not in presets:
        raise ValueError(f"No saved remap preset named {wanted!r} to delete.")
    del presets[wanted]
    _write(path, presets)


def _write(path: Path, presets: dict[str, RemapRules]) -> None:
    """Serialise ``presets`` to ``path`` (creating parent folders)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA_VERSION,
        "presets": {
            name: [list(rule) for rule in rules] for name, rules in presets.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
