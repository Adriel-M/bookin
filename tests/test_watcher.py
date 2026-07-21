"""Targeted unit tests for the watcher's pure decision logic.

These deliberately avoid the concurrency machinery (real Timers, the Observer,
thread shutdown) — that is exercised only end-to-end. What is worth pinning down
here is the extension filtering in ``_schedule`` and the stable-vs-still-changing
decision in ``_check_stable``, which have no timing dependency once the poll
interval is neutralised.
"""

import queue
from pathlib import Path

import pytest

from bookin.watcher import _BookEventHandler


@pytest.fixture
def work_queue():
    return queue.Queue()


@pytest.fixture
def handler(work_queue, mocker):
    # Replace threading.Timer so _schedule never starts a real background thread.
    mocker.patch("bookin.watcher.threading.Timer")
    return _BookEventHandler(work_queue)


# ---------------------------------------------------------------------------
# _schedule — extension filtering
# ---------------------------------------------------------------------------


def test_schedule_ignores_unsupported_extension(handler):
    handler._schedule(Path("/input/notes.txt"))
    assert handler._pending == {}
    from bookin import watcher

    watcher.threading.Timer.assert_not_called()


def test_schedule_accepts_supported_extension(handler):
    path = Path("/input/dune.epub")
    handler._schedule(path)

    assert str(path) in handler._pending
    from bookin import watcher

    watcher.threading.Timer.assert_called_once()
    timer = handler._pending[str(path)]
    timer.start.assert_called_once()


def test_schedule_cancels_existing_timer_for_same_path(handler):
    path = Path("/input/dune.epub")
    handler._schedule(path)
    first_timer = handler._pending[str(path)]
    handler._schedule(path)  # same path again — should cancel the first

    first_timer.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# _check_stable — stable vs still-changing decision
# ---------------------------------------------------------------------------


@pytest.fixture
def no_poll_delay(mocker):
    """Neutralise the inter-read sleep so the size comparison is instantaneous."""
    mocker.patch("bookin.watcher.STABILITY_POLL", 0)


def test_check_stable_queues_when_size_unchanged(handler, work_queue, tmp_path, no_poll_delay):
    book = tmp_path / "dune.epub"
    book.write_bytes(b"stable content")

    handler._check_stable(book)

    assert work_queue.get_nowait() == book
    assert str(book) not in handler._pending


def test_check_stable_skips_missing_file(handler, work_queue, tmp_path, no_poll_delay):
    handler._check_stable(tmp_path / "gone.epub")
    assert work_queue.empty()


def test_check_stable_reschedules_when_still_growing(handler, work_queue, tmp_path, mocker):
    book = tmp_path / "downloading.epub"
    book.write_bytes(b"partial")

    # Simulate the file still being written during the poll: grow it on sleep.
    def grow(_seconds):
        book.write_bytes(b"partial + more bytes")

    mocker.patch("bookin.watcher.time.sleep", side_effect=grow)

    handler._check_stable(book)

    assert work_queue.empty()  # not stable yet
    assert str(book) in handler._pending  # rescheduled for another check
