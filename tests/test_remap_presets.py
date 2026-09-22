"""Tests for the on-disk custom-remap preset store.

:mod:`bal_toolbox_qgis.algorithms._remap_presets` performs no ``qgis`` import
at load time (only :func:`default_presets_path` touches QGIS, lazily), so the
CRUD logic is exercised here in the plain sandbox against a temporary JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bal_toolbox_qgis.algorithms._remap_presets import (
    delete_preset,
    get_preset,
    list_preset_names,
    load_presets,
    save_preset,
)


def _presets_file(tmp_path: Path) -> Path:
    """Return a (non-existent) presets path inside ``tmp_path``."""
    return tmp_path / "nested" / "remap_presets.json"


def test_save_then_get_roundtrip(tmp_path: Path) -> None:
    """Saved rules come back validated (numbers coerced, class an int)."""
    path = _presets_file(tmp_path)
    saved = save_preset(path, "my rules", [[0, 9, 1], [10, 19, 3]])
    assert saved == ((0.0, 9.0, 1), (10.0, 19.0, 3))
    assert get_preset(path, "my rules") == saved
    # The file was created (with parent folders) and is valid JSON.
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert "my rules" in payload["presets"]


def test_missing_file_has_no_presets(tmp_path: Path) -> None:
    """Loading a file that does not exist yields an empty mapping."""
    path = _presets_file(tmp_path)
    assert load_presets(path) == {}
    assert list_preset_names(path) == []


def test_list_names_sorted(tmp_path: Path) -> None:
    """Preset names are returned sorted."""
    path = _presets_file(tmp_path)
    save_preset(path, "zulu", [[1, 1, 1]])
    save_preset(path, "alpha", [[2, 2, 2]])
    assert list_preset_names(path) == ["alpha", "zulu"]


def test_overwrite_existing_preset(tmp_path: Path) -> None:
    """Saving under an existing name overwrites it, keeping others intact."""
    path = _presets_file(tmp_path)
    save_preset(path, "keep", [[1, 1, 1]])
    save_preset(path, "rules", [[0, 5, 2]])
    save_preset(path, "rules", [[0, 9, 4]])
    assert get_preset(path, "rules") == ((0.0, 9.0, 4),)
    assert get_preset(path, "keep") == ((1.0, 1.0, 1),)


def test_get_missing_name_lists_available(tmp_path: Path) -> None:
    """Fetching an unknown preset names the ones that do exist."""
    path = _presets_file(tmp_path)
    save_preset(path, "only", [[1, 1, 1]])
    with pytest.raises(ValueError, match="only"):
        get_preset(path, "absent")


def test_delete_preset(tmp_path: Path) -> None:
    """Deleting removes a preset; deleting an unknown one raises."""
    path = _presets_file(tmp_path)
    save_preset(path, "gone", [[1, 1, 1]])
    delete_preset(path, "gone")
    assert list_preset_names(path) == []
    with pytest.raises(ValueError, match="to delete"):
        delete_preset(path, "gone")


def test_blank_name_rejected(tmp_path: Path) -> None:
    """A blank preset name is rejected on save."""
    path = _presets_file(tmp_path)
    with pytest.raises(ValueError, match="name is required"):
        save_preset(path, "   ", [[1, 1, 1]])


def test_empty_rules_rejected(tmp_path: Path) -> None:
    """An empty rule set is rejected on save."""
    path = _presets_file(tmp_path)
    with pytest.raises(ValueError, match="empty set of remap rules"):
        save_preset(path, "empty", [])


def test_invalid_class_rejected(tmp_path: Path) -> None:
    """A class outside 1-8 is rejected (mirrors the interactive parser)."""
    path = _presets_file(tmp_path)
    with pytest.raises(ValueError, match="whole number"):
        save_preset(path, "bad", [[0, 9, 9]])


def test_corrupt_file_raises(tmp_path: Path) -> None:
    """A file that is not valid JSON surfaces a clear error."""
    path = _presets_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not read remap presets"):
        load_presets(path)


def test_wrong_structure_raises(tmp_path: Path) -> None:
    """A JSON file missing the ``presets`` mapping is rejected."""
    path = _presets_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="expected format"):
        load_presets(path)


def test_hand_edited_bad_rule_raises_on_read(tmp_path: Path) -> None:
    """A stored rule that is not three values is caught on load."""
    path = _presets_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "presets": {"x": [[0, 9]]}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="low, high, class"):
        load_presets(path)
