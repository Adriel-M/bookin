import subprocess
from pathlib import Path

import pytest

from bookin.calibre import (
    calibredb_add,
    calibredb_export,
    calibredb_remove,
    fetch_metadata,
    parse_opf,
    read_embedded_metadata,
    write_metadata,
)
from bookin.errors import CalibreCommandError, CalibreNotFoundError


def _ok(stdout="", stderr="", returncode=0):
    r = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# calibredb_add
# ---------------------------------------------------------------------------


def test_calibredb_add_returns_id(mocker, tmp_path):
    mocker.patch("subprocess.run", return_value=_ok("Added book ids: 7"))
    book_id = calibredb_add(tmp_path / "book.epub", tmp_path)
    assert book_id == 7


def test_calibredb_add_raises_on_failure(mocker, tmp_path):
    mocker.patch("subprocess.run", return_value=_ok(stderr="error", returncode=1))
    with pytest.raises(CalibreCommandError):
        calibredb_add(tmp_path / "book.epub", tmp_path)


def test_calibredb_add_raises_if_no_id_in_output(mocker, tmp_path):
    mocker.patch("subprocess.run", return_value=_ok("Something unexpected"))
    with pytest.raises(CalibreCommandError):
        calibredb_add(tmp_path / "book.epub", tmp_path)


def test_calibredb_add_raises_if_binary_missing(mocker, tmp_path):
    mocker.patch("subprocess.run", side_effect=FileNotFoundError)
    with pytest.raises(CalibreNotFoundError):
        calibredb_add(tmp_path / "book.epub", tmp_path)


# ---------------------------------------------------------------------------
# fetch_metadata
# ---------------------------------------------------------------------------

OPF_CONTENT = '<?xml version="1.0"?><package/>'


def test_fetch_metadata_success(mocker):
    mocker.patch("subprocess.run", return_value=_ok(OPF_CONTENT))
    result = fetch_metadata("Dune", "Frank Herbert", None)
    assert result == OPF_CONTENT


def test_fetch_metadata_uses_isbn_when_available(mocker):
    run_mock = mocker.patch("subprocess.run", return_value=_ok(OPF_CONTENT))
    fetch_metadata(None, None, "9780441013593")
    cmd = run_mock.call_args[0][0]
    assert "--isbn" in cmd
    assert "9780441013593" in cmd


def test_fetch_metadata_returns_none_on_empty_output(mocker):
    mocker.patch("subprocess.run", return_value=_ok(""))
    assert fetch_metadata("Unknown", None, None) is None


def test_fetch_metadata_returns_none_with_no_search_terms(mocker):
    assert fetch_metadata(None, None, None) is None


# ---------------------------------------------------------------------------
# parse_opf
# ---------------------------------------------------------------------------

FULL_OPF = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Dune</dc:title>
    <dc:creator opf:role="aut">Frank Herbert</dc:creator>
    <dc:publisher>Ace Books</dc:publisher>
    <dc:date>1965-08-01</dc:date>
    <dc:identifier opf:scheme="ISBN">9780441013593</dc:identifier>
    <meta name="calibre:series" content="Dune Chronicles"/>
    <meta name="calibre:series_index" content="1"/>
  </metadata>
</package>"""


def test_parse_opf_extracts_all_fields():
    meta = parse_opf(FULL_OPF)
    assert meta["title"] == "Dune"
    assert meta["authors"] == "Frank Herbert"
    assert meta["publisher"] == "Ace Books"
    assert meta["pubdate"] == "1965-08-01"
    assert meta["isbn"] == "9780441013593"
    assert meta["series"] == "Dune Chronicles"
    assert meta["series_index"] == "1"


def test_parse_opf_missing_fields_are_absent():
    meta = parse_opf('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Only Title</dc:title></metadata></package>')
    assert meta["title"] == "Only Title"
    assert meta.get("authors", "") == ""
    assert "series" not in meta


# ---------------------------------------------------------------------------
# write_metadata
# ---------------------------------------------------------------------------


def test_write_metadata_passes_fields(mocker, tmp_path):
    run_mock = mocker.patch("subprocess.run", return_value=_ok())
    write_metadata(tmp_path / "book.epub", {"title": "Dune", "authors": "Frank Herbert", "series": "Dune Chronicles", "series_index": "1"})
    cmd = run_mock.call_args[0][0]
    assert "--title" in cmd
    assert "Dune" in cmd
    assert "--series" in cmd
    assert "--index" in cmd


def test_write_metadata_raises_on_failure(mocker, tmp_path):
    mocker.patch("subprocess.run", return_value=_ok(returncode=1, stderr="err"))
    with pytest.raises(CalibreCommandError):
        write_metadata(tmp_path / "book.epub", {"title": "Dune"})


# ---------------------------------------------------------------------------
# calibredb_export
# ---------------------------------------------------------------------------


def test_calibredb_export_ok(mocker, tmp_path):
    mocker.patch("subprocess.run", return_value=_ok())
    calibredb_export(1, "{authors}/{title}", tmp_path / "out", tmp_path)


def test_calibredb_export_raises_on_failure(mocker, tmp_path):
    mocker.patch("subprocess.run", return_value=_ok(returncode=1, stderr="err"))
    with pytest.raises(CalibreCommandError):
        calibredb_export(1, "{authors}/{title}", tmp_path / "out", tmp_path)


# ---------------------------------------------------------------------------
# calibredb_remove
# ---------------------------------------------------------------------------


def test_calibredb_remove_ok(mocker, tmp_path):
    mocker.patch("subprocess.run", return_value=_ok())
    calibredb_remove(1, tmp_path)  # should not raise


def test_calibredb_remove_logs_warning_on_failure(mocker, tmp_path, caplog):
    mocker.patch("subprocess.run", return_value=_ok(returncode=1, stderr="oops"))
    import logging

    with caplog.at_level(logging.WARNING, logger="bookin.calibre"):
        calibredb_remove(1, tmp_path)  # should not raise


# ---------------------------------------------------------------------------
# read_embedded_metadata
# ---------------------------------------------------------------------------

EBOOK_META_OUTPUT = """\
Title               : Dune
Author(s)           : Frank Herbert
Publisher           : Ace Books
ISBN                : 9780441013593
Published           : 1965-08-01T00:00:00+00:00
"""


def test_read_embedded_metadata_parses_fields(mocker):
    mocker.patch("subprocess.run", return_value=_ok(EBOOK_META_OUTPUT))
    meta = read_embedded_metadata(Path("dummy.epub"))
    assert meta["title"] == "Dune"
    assert meta["authors"] == "Frank Herbert"
    assert meta["isbn"] == "9780441013593"


def test_read_embedded_metadata_returns_empty_on_missing_fields(mocker):
    mocker.patch("subprocess.run", return_value=_ok("Title               : Only Title\n"))
    meta = read_embedded_metadata(Path("dummy.epub"))
    assert meta["title"] == "Only Title"
    assert meta["authors"] == ""
    assert meta["isbn"] == ""
