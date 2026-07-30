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
def failed_dir(tmp_path):
    return tmp_path / "_failed"


@pytest.fixture
def handler(work_queue, failed_dir, mocker):
    # Replace threading.Timer so _schedule never starts a real background thread.
    mocker.patch("bookin.watcher.threading.Timer")
    return _BookEventHandler(work_queue, failed_dir)


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


# ---------------------------------------------------------------------------
# Quarantined files must never be re-queued
# ---------------------------------------------------------------------------


def test_schedule_ignores_files_in_the_failed_dir(handler, failed_dir, mocker):
    # Without this, a dead-lettered file is re-queued, fails again, is moved
    # again under a fresh name, and grows without bound.
    timer = mocker.patch("bookin.watcher.threading.Timer")
    handler._schedule(failed_dir / "dune.epub")
    assert not timer.called


def test_schedule_ignores_files_nested_under_the_failed_dir(handler, failed_dir, mocker):
    timer = mocker.patch("bookin.watcher.threading.Timer")
    handler._schedule(failed_dir / "nested" / "dune.epub")
    assert not timer.called


def test_schedule_still_accepts_files_outside_the_failed_dir(handler, tmp_path, mocker):
    timer = mocker.patch("bookin.watcher.threading.Timer")
    handler._schedule(tmp_path / "dune.epub")
    assert timer.called


def test_schedule_does_not_confuse_a_similar_sibling_name(handler, tmp_path, mocker):
    # "_failed_books" is not the quarantine directory.
    timer = mocker.patch("bookin.watcher.threading.Timer")
    handler._schedule(tmp_path / "_failed_books" / "dune.epub")
    assert timer.called


def test_backfill_skips_the_failed_dir(tmp_path, mocker):
    from bookin.config import Config
    from bookin.watcher import run_daemon

    (tmp_path / "_failed").mkdir()
    (tmp_path / "_failed" / "broken.epub").write_bytes(b"x")
    (tmp_path / "good.epub").write_bytes(b"x")

    queued = []
    mocker.patch("bookin.watcher.check_calibre")
    mocker.patch("bookin.watcher.threading.Thread")
    mocker.patch("bookin.watcher.Observer", side_effect=RuntimeError("stop after backfill"))
    mocker.patch.object(queue.Queue, "put", side_effect=lambda p: queued.append(p))

    cfg = Config(input_dir=tmp_path, output_dir=tmp_path / "out")
    with pytest.raises(RuntimeError):
        run_daemon(cfg)

    names = [p.name for p in queued if p is not None]
    assert "good.epub" in names
    assert "broken.epub" not in names, "Restart must not retry quarantined files"
