"""Thin subprocess wrappers around Calibre CLI tools.

Calibre handles metadata embedding, library add and template-driven export.
Metadata *lookup* lives in bookin.hardcover, which talks to the API directly.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

from bookin.errors import CalibreCommandError, CalibreNotFoundError

log = logging.getLogger("bookin.calibre")


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a command, raising CalibreNotFoundError if the binary is missing."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as err:
        raise CalibreNotFoundError(
            f"Command not found: {cmd[0]!r}. Is Calibre installed and on PATH?"
        ) from err
    except subprocess.TimeoutExpired as err:
        raise CalibreCommandError(f"Command timed out after {timeout}s: {cmd[0]!r}") from err
    return result


def check_calibre() -> None:
    """Raise CalibreNotFoundError if the CLI tools the pipeline needs are missing."""
    for binary in ("calibredb", "ebook-meta"):
        if not shutil.which(binary):
            raise CalibreNotFoundError(f"{binary!r} not found. Is Calibre installed and on PATH?")


def calibredb_add(file: Path, library: Path) -> int:
    """Add a book to a Calibre library. Returns the numeric book ID."""
    result = _run(["calibredb", "add", str(file), "--with-library", str(library)])
    if result.returncode != 0:
        raise CalibreCommandError(f"calibredb add failed:\n{result.stderr}")

    match = re.search(r"Added book ids:\s*(\d+)", result.stdout)
    if not match:
        raise CalibreCommandError(
            f"Could not parse book ID from calibredb output:\n{result.stdout}"
        )
    book_id = int(match.group(1))
    log.debug("Added to library as ID %d: %s", book_id, file.name)
    return book_id


def write_metadata(file: Path, meta: dict[str, str], cover: Path | None = None) -> None:
    """Embed metadata into an ebook file using ebook-meta.

    If ``cover`` is given, the image is embedded into the file as its cover.
    """
    cmd = ["ebook-meta", str(file)]
    if meta.get("title"):
        cmd += ["--title", meta["title"]]
    if meta.get("authors"):
        cmd += ["--authors", meta["authors"]]
    if meta.get("isbn"):
        cmd += ["--isbn", meta["isbn"]]
    if meta.get("publisher"):
        cmd += ["--publisher", meta["publisher"]]
    if meta.get("pubdate"):
        # ebook-meta's flag for the published date is --date (--pubdate is a
        # calibredb option and makes ebook-meta error out).
        cmd += ["--date", meta["pubdate"]]
    if meta.get("series"):
        cmd += ["--series", meta["series"]]
    if meta.get("series_index"):
        cmd += ["--index", meta["series_index"]]
    if cover:
        cmd += ["--cover", str(cover)]

    result = _run(cmd, timeout=60)
    if result.returncode != 0:
        raise CalibreCommandError(f"ebook-meta write failed:\n{result.stderr}")
    log.debug("Embedded metadata into %s", file.name)


def calibredb_export(
    book_id: int,
    template: str,
    dest_dir: Path,
    library: Path,
) -> None:
    """Export a book from the library using a Calibre template pattern."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            "calibredb",
            "export",
            str(book_id),
            "--template",
            template,
            "--to-dir",
            str(dest_dir),
            "--dont-save-cover",
            "--dont-write-opf",
            "--with-library",
            str(library),
        ]
    )
    if result.returncode != 0:
        raise CalibreCommandError(f"calibredb export failed:\n{result.stderr}")
    log.debug("Exported book ID %d to %s", book_id, dest_dir)


def calibredb_remove(book_id: int, library: Path) -> None:
    """Remove a book from the library (cleanup after export)."""
    result = _run(
        [
            "calibredb",
            "remove",
            str(book_id),
            "--with-library",
            str(library),
        ]
    )
    if result.returncode != 0:
        log.warning("calibredb remove failed (non-fatal): %s", result.stderr.strip())


def read_embedded_metadata(file: Path) -> dict[str, str]:
    """Read metadata embedded in an ebook file. Returns title, authors, isbn (may be empty)."""
    result = _run(["ebook-meta", str(file)], timeout=30)
    meta: dict[str, str] = {"title": "", "authors": "", "isbn": ""}

    for line in result.stdout.splitlines():
        match = re.match(r"^([^:]+?)\s*:\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1).strip().lower(), match.group(2).strip()
        if key == "title":
            meta["title"] = value
        elif key in ("author(s)", "authors"):
            # ebook-meta appends the author-sort in brackets, e.g.
            # "Matt Dinniman [Dinniman, Matt]" — drop it so the value is just
            # the display name(s) and doesn't pollute the metadata query.
            meta["authors"] = re.sub(r"\s*\[[^\]]*\]", "", value).strip()
        elif key == "identifiers":
            # ebook-meta reports every identifier on one line, e.g.
            # "Identifiers : isbn:9780525537113, amazon:0525537112". There is
            # no bare "ISBN" line to key off, so pull it out of this list.
            meta["isbn"] = _isbn_from_identifiers(value)
        elif key == "isbn" and value:
            meta["isbn"] = _normalize_isbn(value)

    return meta


def _isbn_from_identifiers(value: str) -> str:
    """Extract the ISBN from ebook-meta's comma-separated identifier list."""
    for identifier in value.split(","):
        scheme, _, number = identifier.strip().partition(":")
        if scheme.strip().lower() == "isbn" and number.strip():
            return _normalize_isbn(number)
    return ""


def _normalize_isbn(isbn: str) -> str:
    """Strip formatting from an ISBN.

    Files often embed the hyphenated form ("978-0-307-49846-5"), but metadata
    sources index the plain digits, so an exact-match lookup on the raw value
    silently finds nothing. The ISBN-10 check digit may be 'X'.
    """
    return re.sub(r"[^0-9Xx]", "", isbn).upper()
