"""Pure-Python checks for the shared algorithm help URL.

``_help`` imports no ``qgis``, so this runs in the plain sandbox and in CI
without a QGIS runtime. The QGIS-side test (``test_qgis_glue``) separately
asserts that every algorithm's ``helpUrl()`` returns this same constant.
"""

from __future__ import annotations

from pathlib import Path

from bal_toolbox_qgis.algorithms._help import HELP_URL


def test_help_url_is_a_plausible_https_url() -> None:
    """The help URL is an absolute https link into the project."""
    assert HELP_URL.startswith("https://")
    assert "bal_toolbox_qgis" in HELP_URL


def test_help_url_lives_under_declared_repository() -> None:
    """The help URL must sit under the repository declared in metadata.txt.

    Guards against the two drifting apart if the repository is ever renamed.
    """
    metadata = (
        Path(__file__).resolve().parent.parent / "bal_toolbox_qgis" / "metadata.txt"
    ).read_text(encoding="utf-8")
    repo_line = next(
        line for line in metadata.splitlines() if line.startswith("repository=")
    )
    repository = repo_line.split("=", 1)[1].strip()
    assert HELP_URL.startswith(repository)
