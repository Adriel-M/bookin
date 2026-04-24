"""Folder watcher daemon using watchdog with file stability checking."""

import logging
import queue
import threading
import time
from pathlib import Path

from watchdog.events import (
    DirCreatedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from bookin.calibre import check_calibre
from bookin.config import STABILITY_WAIT, SUPPORTED_EXTENSIONS, Config
from bookin.processor import process_file

log = logging.getLogger("bookin.watcher")


class _BookEventHandler(FileSystemEventHandler):
    """Watchdog event handler that queues stable ebook files for processing."""

    def __init__(self, work_queue: queue.Queue[Path | None]) -> None:
        self._queue = work_queue
        self._pending: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent) -> None:
        if event.is_directory:
            return
        self._schedule(Path(str(event.src_path)))

    def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
        if event.is_directory:
            return
        self._schedule(Path(str(event.dest_path)))

    def _schedule(self, path: Path) -> None:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        key = str(path)
        with self._lock:
            if key in self._pending:
                self._pending[key].cancel()
            timer = threading.Timer(STABILITY_WAIT, self._check_stable, args=[path])
            self._pending[key] = timer
            timer.start()
        log.debug("Scheduled stability check for %s", path.name)

    def _check_stable(self, path: Path) -> None:
        key = str(path)
        with self._lock:
            self._pending.pop(key, None)

        if not path.exists():
            log.debug("File disappeared before processing: %s", path.name)
            return

        try:
            size_before = path.stat().st_size
            time.sleep(1)
            size_after = path.stat().st_size
        except OSError:
            log.warning("Could not stat %s — skipping", path.name)
            return

        if size_before == size_after:
            log.info("Queuing: %s", path.name)
            self._queue.put(path)
        else:
            # Still being written — reschedule
            log.debug("File still changing, rescheduling: %s", path.name)
            with self._lock:
                timer = threading.Timer(STABILITY_WAIT, self._check_stable, args=[path])
                self._pending[key] = timer
                timer.start()


def _worker(work_queue: queue.Queue[Path | None], cfg: Config) -> None:
    """Consume from the work queue and process files one at a time."""
    while True:
        path = work_queue.get()
        if path is None:  # sentinel: shut down
            break
        try:
            process_file(path, cfg)
        except Exception as exc:
            log.exception("Unexpected error processing %s: %s", path.name, exc)
        finally:
            work_queue.task_done()


def run_daemon(cfg: Config) -> None:
    """Start the folder watcher daemon. Blocks until interrupted."""
    check_calibre()

    cfg.input_dir.mkdir(parents=True, exist_ok=True)
    log.info("Watching %s for ebooks...", cfg.input_dir)

    work_queue: queue.Queue[Path | None] = queue.Queue()
    handler = _BookEventHandler(work_queue)

    worker_thread = threading.Thread(target=_worker, args=(work_queue, cfg), daemon=True)
    worker_thread.start()

    for path in sorted(cfg.input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            log.info("Queuing existing file: %s", path.name)
            work_queue.put(path)

    observer = Observer()
    observer.schedule(handler, str(cfg.input_dir), recursive=True)
    observer.start()

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        observer.stop()
        observer.join()
        work_queue.put(None)  # stop worker
        worker_thread.join(timeout=5)
        if worker_thread.is_alive():
            log.warning(
                "Worker thread did not shut down cleanly — in-progress file may be incomplete"
            )
