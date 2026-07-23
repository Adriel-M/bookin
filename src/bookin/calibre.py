"""Thin subprocess wrappers around Calibre CLI tools."""

import atexit
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import defusedxml.ElementTree as ET

from bookin.errors import CalibreCommandError, CalibreNotFoundError

log = logging.getLogger("bookin.calibre")

# Metadata source. The Hardcover plugin is third-party, so it is baked into the
# image as a zip and installed at startup (see configure_hardcover). The zip
# path is overridable for local development.
HARDCOVER_PLUGIN_NAME = "Hardcover"
HARDCOVER_PLUGIN_ZIP = os.environ.get("BOOKIN_HARDCOVER_PLUGIN", "/opt/hardcover.zip")


def configure_hardcover(token: str) -> None:
    """Install the Hardcover metadata plugin and seed its API token.

    Creates a throwaway ``CALIBRE_CONFIG_DIRECTORY`` (exported into the
    environment so every Calibre subprocess inherits it), installs the plugin
    into it, and writes the API token to the plugin's on-disk config.

    The token is written *only* to that config file — it is never logged and
    never passed on a command line (so it can't leak via log output or a
    process listing).
    """
    cfg_dir = tempfile.mkdtemp(prefix="bookin_calibre_")
    os.environ["CALIBRE_CONFIG_DIRECTORY"] = cfg_dir
    atexit.register(shutil.rmtree, cfg_dir, ignore_errors=True)

    result = _run(["calibre-customize", "-a", HARDCOVER_PLUGIN_ZIP])
    if result.returncode != 0:
        raise CalibreCommandError(f"Failed to install Hardcover plugin:\n{result.stderr}")

    sources_dir = Path(cfg_dir) / "metadata_sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / f"{HARDCOVER_PLUGIN_NAME}.json").write_text(json.dumps({"api_key": token}))
    log.info("Hardcover metadata source configured")


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
    """Raise CalibreNotFoundError if calibredb or fetch-ebook-metadata are missing."""
    for binary in ("calibredb", "fetch-ebook-metadata"):
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


def fetch_metadata(
    title: str | None,
    authors: str | None,
    isbn: str | None,
    cover_path: Path | None = None,
) -> str | None:
    """Fetch metadata from Hardcover. Returns OPF content as a string, or None if not found.

    If ``cover_path`` is given, the cover image (when found) is downloaded to that path.
    Requires the Hardcover plugin to have been configured via configure_hardcover.
    """
    cmd = ["fetch-ebook-metadata", "--allowed-plugin", HARDCOVER_PLUGIN_NAME, "--opf"]
    if cover_path is not None:
        cmd += ["--cover", str(cover_path)]
    if isbn:
        cmd += ["--isbn", isbn]
        query = f"isbn={isbn!r}"
    elif title:
        cmd += ["--title", title]
        query = f"title={title!r}"
        if authors:
            cmd += ["--authors", authors]
            query += f" authors={authors!r}"
    else:
        log.warning("No title, author, or ISBN — skipping metadata fetch")
        return None

    log.info("Querying Hardcover: %s", query)
    result = _run(cmd, timeout=120)

    stderr = result.stderr.strip()
    if result.returncode != 0:
        log.warning(
            "Hardcover lookup failed (exit %d) for %s: %s",
            result.returncode,
            query,
            stderr or "<no stderr>",
        )
        return None

    if not result.stdout.strip():
        # Exit 0 with no output means the plugin ran but found no matching book.
        log.warning(
            "Hardcover found no match for %s%s",
            query,
            f" (stderr: {stderr})" if stderr else "",
        )
        return None

    log.info("Hardcover returned metadata for %s", query)
    log.debug("OPF payload:\n%s", result.stdout)
    return result.stdout


_OPF_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf",
}


def parse_opf(opf_content: str) -> dict[str, str]:
    """Extract metadata fields from OPF XML content."""
    root = ET.fromstring(opf_content)
    meta: dict[str, str] = {}

    def _text(tag: str) -> str:
        el = root.find(f".//{tag}", _OPF_NS)
        return el.text.strip() if el is not None and el.text else ""

    meta["title"] = _text("dc:title")
    meta["authors"] = _text("dc:creator")
    meta["publisher"] = _text("dc:publisher")
    meta["pubdate"] = _text("dc:date")

    for el in root.findall(".//dc:identifier", _OPF_NS):
        scheme = el.get("{http://www.idpf.org/2007/opf}scheme", "") or el.get("scheme", "")
        if "isbn" in scheme.lower() and el.text:
            meta["isbn"] = el.text.strip()
            break

    for el in root.findall(".//{http://www.idpf.org/2007/opf}meta"):
        name = el.get("name", "")
        content = el.get("content", "").strip()
        if name == "calibre:series":
            meta["series"] = content
        elif name == "calibre:series_index":
            meta["series_index"] = content

    return meta


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
        elif key == "isbn":
            meta["isbn"] = value

    return meta
