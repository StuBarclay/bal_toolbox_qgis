"""Glue-level progress reporting and cancellation for the BAL directional search.

The vendored compute core (:mod:`bal_toolbox_qgis.balcore.engine` /
:mod:`~bal_toolbox_qgis.balcore.workflow`) is kept byte-for-byte identical to the
upstream project, so it exposes no hook for progress reporting or cooperative
cancellation. Rather than fork the verified numerics, this module temporarily
wraps :func:`bal_toolbox_qgis.balcore.engine.compute_direction` -- which the
core's :func:`~bal_toolbox_qgis.balcore.engine.compute_bal` calls exactly once
per compass direction -- so each of the eight directions advances a QGIS
``QgsProcessingFeedback`` progress bar and the run honours
``feedback.isCanceled()``. The original function is always restored on exit, so
the patch is invisible outside the ``with`` block and leaves the core untouched.

The wrapper only ever calls the genuine ``compute_direction`` and returns its
result unchanged, so the BAL numerics are never altered -- it adds reporting and
a cancellation check around the existing computation.

This module deliberately performs no ``qgis`` import: it needs only the
duck-typed feedback protocol (``isCanceled`` / ``setProgress`` / optional
``setProgressText``), so it imports and unit-tests without a QGIS runtime, just
like the compute core.

.. note::
   The wrap swaps a module-level attribute, so two BAL runs executing
   concurrently on Processing worker threads could momentarily cross-report
   progress. The BAL *results* are never affected (the wrapper always defers to
   the real function); only the progress-bar cosmetics could glitch in that rare
   case.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from bal_toolbox_qgis.balcore import engine


class BalCanceled(Exception):  # noqa: N818 - not an error condition; a control signal
    """Raised inside the directional search when the user cancels the run.

    Caught by the calling algorithm, which then stops cleanly and returns no
    outputs rather than surfacing this as a processing error.
    """


def _is_canceled(feedback: Any) -> bool:
    """Return whether ``feedback`` reports the run as cancelled (safely)."""
    is_canceled = getattr(feedback, "isCanceled", None)
    if callable(is_canceled):
        return bool(is_canceled())
    return False


def _set_progress(feedback: Any, value: float) -> None:
    """Set ``feedback`` progress to ``value`` percent, clamped to 0-100."""
    setter = getattr(feedback, "setProgress", None)
    if callable(setter):
        setter(max(0.0, min(100.0, float(value))))


def _set_progress_text(feedback: Any, text: str) -> None:
    """Set ``feedback`` progress text when the sink supports it."""
    setter = getattr(feedback, "setProgressText", None)
    if callable(setter):
        setter(text)


@contextlib.contextmanager
def direction_progress(
    feedback: Any,
    start: float = 10.0,
    end: float = 90.0,
) -> Iterator[None]:
    """Report per-direction progress and honour cancellation during ``compute_bal``.

    Wraps :func:`engine.compute_direction` for the duration of the ``with``
    block. Each completed compass direction advances ``feedback`` linearly from
    ``start`` to ``end`` percent, and a pending cancellation raises
    :class:`BalCanceled` before the next direction is computed. The original
    function is restored on exit -- including when the body raises -- so the core
    is left exactly as found.

    Args:
        feedback: A ``QgsProcessingFeedback`` (or any object exposing
            ``isCanceled`` / ``setProgress`` / optionally ``setProgressText``).
            ``None`` and partial objects are tolerated.
        start: Progress percent already reached when the search begins.
        end: Progress percent reached once all directions are computed.

    Yields:
        ``None`` -- run the BAL workflow inside the ``with`` block.

    Raises:
        BalCanceled: When ``feedback.isCanceled()`` becomes true between
            directions.
    """
    original = engine.compute_direction
    total = len(engine.DIRECTIONS)
    span = end - start
    done = 0

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        nonlocal done
        if _is_canceled(feedback):
            raise BalCanceled
        result = original(*args, **kwargs)
        done += 1
        _set_progress(feedback, start + span * done / total)
        _set_progress_text(feedback, f"Computed {done} of {total} BAL directions")
        return result

    engine.compute_direction = wrapped
    try:
        yield
    finally:
        engine.compute_direction = original
