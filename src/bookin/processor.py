"""Per-file processing pipeline."""

import logging
import shutil
import tempfile
import traceback
from pathlib import Path

from bookin.calibre import (
    calibredb_add,
    calibredb_export,
    calibredb_remove,
    fetch_metadata,
    parse_opf,
    read_embedded_metadata,
    write_metadata,
)
from bookin.config import INPUT_DIR, OUTPUT_DIR, SUPPORTED_EXTENSIONS, Config

log = logging.getLogger("bookin.processor")


def process_file(file: Path, cfg: Config) -> None:
    log.info("Processing: %s", file.name)
    tmp_dir = Path(tempfile.mkdtemp(prefix="bookin_"))

    try:
        _process(file, cfg, tmp_dir)
    except Exception as exc:
        log.error("Failed to process %s: %s", file.name, exc)
        _move_to_failed(file, exc)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _process(file: Path, cfg: Config, tmp_dir: Path) -> None:
    if not file.exists():
        log.warning("File disappeared before processing: %s", file.name)
        return

    embedded = read_embedded_metadata(file)
    title = embedded.get("title") or file.stem
    authors = embedded.get("authors") or None
    isbn = embedded.get("isbn") or None

    opf = fetch_metadata(title, authors, isbn)
    if opf:
        try:
            write_metadata(file, parse_opf(opf))
        except Exception as exc:
            log.warning("Could not embed metadata (continuing without): %s", exc)

    library_dir = tmp_dir / "library"
    library_dir.mkdir()
    book_id = calibredb_add(file, library_dir)

    calibredb_export(book_id, cfg.template, OUTPUT_DIR, library_dir)
    calibredb_remove(book_id, library_dir)

    file.unlink()
    _cleanup_dirs(file.parent)
    log.info("Done: %s", file.name)


def _cleanup_dirs(directory: Path) -> None:
    """Remove directories with no eligible files bottom-up, stopping at INPUT_DIR."""
    current = directory
    while current != INPUT_DIR and INPUT_DIR in current.parents:
        has_eligible = any(
            p.suffix.lower() in SUPPORTED_EXTENSIONS for p in current.rglob("*") if p.is_file()
        )
        if has_eligible:
            break
        try:
            shutil.rmtree(current)
            log.debug("Removed directory: %s", current.name)
        except OSError as exc:
            log.warning("Could not remove %s: %s", current, exc)
            break
        current = current.parent


def _move_to_failed(file: Path, exc: Exception) -> None:
    """Move a failed file to /output/_failed/ with an error sidecar."""
    failed_dir = OUTPUT_DIR / "_failed"
    failed_dir.mkdir(parents=True, exist_ok=True)

    dest = failed_dir / file.name
    counter = 1
    while dest.exists():
        dest = failed_dir / f"{file.stem}_{counter}{file.suffix}"
        counter += 1

    try:
        shutil.move(str(file), dest)
    except OSError as move_err:
        log.error("Could not move failed file %s: %s", file, move_err)
        return

    log.error("Moved failed file to %s", dest)
    try:
        dest.with_suffix(dest.suffix + ".error").write_text(
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        )
    except OSError as sidecar_err:
        log.error("Could not write error sidecar for %s: %s", dest, sidecar_err)
