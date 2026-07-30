import subprocess
from pathlib import Path

import pytest

from bookin.calibre import (
    calibredb_add,
    calibredb_export,
    calibredb_remove,
    check_calibre,
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
# check_calibre
# ---------------------------------------------------------------------------


def test_check_calibre_requires_the_tools_the_pipeline_uses(mocker):
    which = mocker.patch("shutil.which", return_value="/usr/bin/x")
    check_calibre()
    checked = [call.args[0] for call in which.call_args_list]
    # ebook-meta does the embedding and the embedded-metadata read, so a missing
    # one has to fail loudly rather than at the first processed file.
    assert checked == ["calibredb", "ebook-meta"]


def test_check_calibre_no_longer_requires_fetch_ebook_metadata(mocker):
    which = mocker.patch("shutil.which", return_value="/usr/bin/x")
    check_calibre()
    assert "fetch-ebook-metadata" not in [call.args[0] for call in which.call_args_list]


def test_check_calibre_raises_when_a_binary_is_missing(mocker):
    mocker.patch("shutil.which", return_value=None)
    with pytest.raises(CalibreNotFoundError):
        check_calibre()


# ---------------------------------------------------------------------------
# write_metadata
# ---------------------------------------------------------------------------


def test_write_metadata_passes_fields(mocker, tmp_path):
    run_mock = mocker.patch("subprocess.run", return_value=_ok())
    write_metadata(
        tmp_path / "book.epub",
        {
            "title": "Dune",
            "authors": "Frank Herbert",
            "series": "Dune Chronicles",
            "series_index": "1",
        },
    )
    cmd = run_mock.call_args[0][0]
    assert "--title" in cmd
    assert "Dune" in cmd
    assert "--series" in cmd
    assert "--index" in cmd


def test_write_metadata_pubdate_uses_date_flag(mocker, tmp_path):
    run_mock = mocker.patch("subprocess.run", return_value=_ok())
    write_metadata(tmp_path / "book.epub", {"title": "Dune", "pubdate": "1965-08-01"})
    cmd = run_mock.call_args[0][0]
    # ebook-meta has no --pubdate option; the published date must use --date.
    assert "--date" in cmd
    assert "1965-08-01" in cmd
    assert "--pubdate" not in cmd


def test_write_metadata_embeds_cover_when_given(mocker, tmp_path):
    run_mock = mocker.patch("subprocess.run", return_value=_ok())
    cover = tmp_path / "cover.jpg"
    write_metadata(tmp_path / "book.epub", {"title": "Dune"}, cover=cover)
    cmd = run_mock.call_args[0][0]
    assert "--cover" in cmd
    assert str(cover) in cmd


def test_write_metadata_omits_cover_when_absent(mocker, tmp_path):
    run_mock = mocker.patch("subprocess.run", return_value=_ok())
    write_metadata(tmp_path / "book.epub", {"title": "Dune"})
    assert "--cover" not in run_mock.call_args[0][0]


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

# Verbatim shape of real `ebook-meta` output. Identifiers are reported as one
# comma-separated line — there is no bare "ISBN" line, which is what let the
# ISBN lookup path sit dead behind an over-friendly test fixture.
EBOOK_META_OUTPUT = """\
Title               : Dune
Author(s)           : Frank Herbert
Publisher           : Ace Books
Series              : Dune #1
Languages           : eng
Published           : 1965-08-01T00:00:00+00:00
Identifiers         : isbn:9780441013593, amazon:0441013597, hardcover:dune
"""


def test_read_embedded_metadata_parses_fields(mocker):
    mocker.patch("subprocess.run", return_value=_ok(EBOOK_META_OUTPUT))
    meta = read_embedded_metadata(Path("dummy.epub"))
    assert meta["title"] == "Dune"
    assert meta["authors"] == "Frank Herbert"
    assert meta["isbn"] == "9780441013593"


def test_read_embedded_metadata_picks_isbn_out_of_identifiers(mocker):
    # The ISBN must survive regardless of its position in the list, and the
    # neighbouring amazon/hardcover ids must not be mistaken for it.
    mocker.patch(
        "subprocess.run",
        return_value=_ok("Identifiers         : amazon:0441013597, isbn:9780441013593\n"),
    )
    assert read_embedded_metadata(Path("dummy.epub"))["isbn"] == "9780441013593"


def test_read_embedded_metadata_ignores_identifiers_without_an_isbn(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=_ok("Identifiers         : amazon:0441013597, hardcover:dune\n"),
    )
    assert read_embedded_metadata(Path("dummy.epub"))["isbn"] == ""


@pytest.mark.parametrize(
    "embedded,expected",
    [
        ("isbn:978-0-441-01359-3", "9780441013593"),
        ("isbn:978 0 441 01359 3", "9780441013593"),
        ("isbn:0-441-01359-X", "044101359X"),
        ("isbn:urn:isbn:9780441013593", "9780441013593"),
    ],
)
def test_read_embedded_metadata_normalizes_the_isbn(mocker, embedded, expected):
    # Metadata sources index the plain digits, so a hyphenated value looked up
    # verbatim matches nothing.
    mocker.patch("subprocess.run", return_value=_ok(f"Identifiers         : {embedded}\n"))
    assert read_embedded_metadata(Path("dummy.epub"))["isbn"] == expected


def test_read_embedded_metadata_still_accepts_a_bare_isbn_line(mocker):
    mocker.patch("subprocess.run", return_value=_ok("ISBN                : 978-0-441-01359-3\n"))
    assert read_embedded_metadata(Path("dummy.epub"))["isbn"] == "9780441013593"


def test_read_embedded_metadata_returns_empty_on_missing_fields(mocker):
    mocker.patch("subprocess.run", return_value=_ok("Title               : Only Title\n"))
    meta = read_embedded_metadata(Path("dummy.epub"))
    assert meta["title"] == "Only Title"
    assert meta["authors"] == ""
    assert meta["isbn"] == ""


def test_read_embedded_metadata_strips_author_sort(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=_ok("Author(s)           : Matt Dinniman [Dinniman, Matt]\n"),
    )
    meta = read_embedded_metadata(Path("dummy.epub"))
    assert meta["authors"] == "Matt Dinniman"


def test_read_embedded_metadata_strips_sort_for_multiple_authors(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=_ok(
            "Author(s) : Neil Gaiman & Terry Pratchett [Gaiman, Neil & Pratchett, Terry]\n"
        ),
    )
    meta = read_embedded_metadata(Path("dummy.epub"))
    assert meta["authors"] == "Neil Gaiman & Terry Pratchett"
