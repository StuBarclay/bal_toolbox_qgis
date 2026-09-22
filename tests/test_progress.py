"""Tests for the glue-level progress / cancellation wrapper.

These exercise :mod:`bal_toolbox_qgis.algorithms._progress`, which is
deliberately ``qgis``-free (it needs only the duck-typed feedback protocol),
so the whole module runs in the plain sandbox. They drive the *real*
:func:`bal_toolbox_qgis.balcore.engine.compute_bal` on a tiny raster to prove
the monkeypatch is actually picked up by the core's per-direction loop, and
that the original function is always restored afterwards.
"""

from __future__ import annotations

import numpy as np
import pytest
from bal_toolbox_qgis.algorithms._progress import (
    BalCanceled,
    direction_progress,
)
from bal_toolbox_qgis.balcore import engine
from numpy.typing import NDArray


class FakeFeedback:
    """A minimal ``QgsProcessingFeedback`` stand-in that records calls.

    Args:
        cancel_after: When set, ``isCanceled`` starts returning ``True`` once
            this many directions have completed (i.e. that many
            ``setProgress`` calls have been recorded), simulating a user
            cancelling mid-run.
    """

    def __init__(self, cancel_after: int | None = None) -> None:
        self.progress: list[float] = []
        self.texts: list[str] = []
        self._cancel_after = cancel_after

    def isCanceled(self) -> bool:  # noqa: N802 - QGIS feedback protocol name
        if self._cancel_after is None:
            return False
        return len(self.progress) >= self._cancel_after

    def setProgress(self, value: float) -> None:  # noqa: N802 - QGIS name
        self.progress.append(value)

    def setProgressText(self, text: str) -> None:  # noqa: N802 - QGIS name
        self.texts.append(text)


def _tiny_inputs() -> tuple[
    NDArray[np.int_], NDArray[np.int_], NDArray[np.int_], float, int
]:
    """Return small, valid ``compute_bal`` inputs (veg/slope/aspect + args)."""
    veg = np.full((4, 4), 2, dtype=np.int_)
    slope_band = np.full((4, 4), 1, dtype=np.int_)
    aspect_band = np.full((4, 4), 1, dtype=np.int_)
    return veg, slope_band, aspect_band, 30.0, 50


def test_full_run_reports_progress_per_direction() -> None:
    """A complete run advances the bar once per direction, ending at ``end``."""
    veg, slope_band, aspect_band, pixel_width, fdi = _tiny_inputs()
    feedback = FakeFeedback()
    original = engine.compute_direction

    with direction_progress(feedback, start=10.0, end=90.0):
        engine.compute_bal(veg, slope_band, aspect_band, pixel_width, fdi)

    # One progress step per compass direction, monotonic, finishing at 90.
    assert len(feedback.progress) == len(engine.DIRECTIONS)
    assert feedback.progress == sorted(feedback.progress)
    assert feedback.progress[0] == pytest.approx(10.0 + 80.0 / len(engine.DIRECTIONS))
    assert feedback.progress[-1] == pytest.approx(90.0)
    assert len(feedback.texts) == len(engine.DIRECTIONS)
    assert (
        feedback.texts[-1] == f"Computed {len(engine.DIRECTIONS)} of 8 BAL directions"
    )
    # The core function is restored exactly as found.
    assert engine.compute_direction is original


def test_cancellation_raises_and_restores() -> None:
    """Cancelling mid-run raises ``BalCanceled`` and restores the core."""
    veg, slope_band, aspect_band, pixel_width, fdi = _tiny_inputs()
    feedback = FakeFeedback(cancel_after=3)
    original = engine.compute_direction

    with pytest.raises(BalCanceled):  # noqa: SIM117 - two distinct contexts
        with direction_progress(feedback, start=10.0, end=90.0):
            engine.compute_bal(veg, slope_band, aspect_band, pixel_width, fdi)

    # Exactly three directions completed before the cancel took effect.
    assert len(feedback.progress) == 3
    # The original function is restored even though the body raised.
    assert engine.compute_direction is original


def test_tolerates_partial_feedback_object() -> None:
    """A feedback object missing optional hooks does not break the run."""
    veg, slope_band, aspect_band, pixel_width, fdi = _tiny_inputs()

    class Bare:
        """Only exposes ``isCanceled``; no progress setters."""

        def isCanceled(self) -> bool:  # noqa: N802 - QGIS name
            return False

    original = engine.compute_direction
    with direction_progress(Bare(), start=0.0, end=100.0):
        result = engine.compute_bal(veg, slope_band, aspect_band, pixel_width, fdi)

    assert "max" in result
    assert engine.compute_direction is original
