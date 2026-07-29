import logging
from pathlib import Path

import pytest

from bookin.config import Config
from bookin.errors import CalibreCommandError
from bookin.processor import process_file


@pytest.fixture
def epub_file(tmp_path):
    f = tmp_path / "dune.epub"
    f.write_bytes(b"fake epub content")
    return f


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output"
    d.mkdir()
    return d


@pytest.fixture
def cfg(tmp_path, output_dir):
    return Config(template="{authors}/{title}", input_dir=tmp_path, output_dir=output_dir)


@pytest.fixture(autouse=True)
def patch_output_dir(output_dir):
    return output_dir


def _make_calibre_mocks(
    mocker,
    *,
    fetch_ok=True,
    write_meta_ok=True,
    export_ok=True,
    export_name="Frank Herbert/Dune.epub",
):
    mocker.patch("bookin.processor.calibredb_add", return_value=1)
    mocker.patch(
        "bookin.processor.read_embedded_metadata",
        return_value={"title": "Dune", "authors": "Frank Herbert", "isbn": ""},
    )
    mocker.patch(
        "bookin.processor.fetch_metadata",
        return_value={"title": "Dune", "authors": "Frank Herbert"} if fetch_ok else None,
    )
    mocker.patch(
        "bookin.processor.write_metadata",
        side_effect=None if write_meta_ok else CalibreCommandError("write failed"),
    )
    mocker.patch(
        "bookin.processor.calibredb_export",
        side_effect=_fake_export(export_name)
        if export_ok
        else CalibreCommandError("export failed"),
    )
    mocker.patch("bookin.processor.calibredb_remove")


def _fake_export(name="Frank Herbert/Dune.epub"):
    """Stand in for calibredb, which renders the template into dest_dir itself."""

    def export(_book_id, _template, dest_dir, _library):
        target = Path(dest_dir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"exported epub")

    return export


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_process_file_success_deletes_source(mocker, epub_file, cfg):
    _make_calibre_mocks(mocker)
    process_file(epub_file, cfg)
    assert not epub_file.exists(), "Source file should be deleted after successful export"


def test_process_file_calls_export_with_template(mocker, epub_file, cfg):
    _make_calibre_mocks(mocker)
    export_mock = mocker.patch("bookin.processor.calibredb_export")
    process_file(epub_file, cfg)
    assert export_mock.called
    assert cfg.template in export_mock.call_args[0]


# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------


def test_process_file_embeds_cover_when_downloaded(mocker, epub_file, cfg):
    _make_calibre_mocks(mocker)

    # Record the cover argument (and its existence) at call time, since the temp
    # dir holding it is cleaned up once process_file returns.
    seen = {}

    def record_write(_file, _meta, cover=None):
        seen["cover"] = cover
        seen["exists"] = cover is not None and cover.exists()

    mocker.patch("bookin.processor.write_metadata", side_effect=record_write)

    # Simulate fetch_metadata downloading the cover to the path it is given.
    def fake_fetch(title, authors, isbn, cover_path):
        cover_path.write_bytes(b"jpeg")
        return {"title": "Dune", "authors": "Frank Herbert"}

    mocker.patch("bookin.processor.fetch_metadata", side_effect=fake_fetch)
    process_file(epub_file, cfg)

    assert seen["cover"] is not None
    assert seen["exists"]


def test_process_file_no_cover_when_not_downloaded(mocker, epub_file, cfg):
    _make_calibre_mocks(mocker)  # mocked fetch does not create a cover file
    write_mock = mocker.patch("bookin.processor.write_metadata")
    process_file(epub_file, cfg)

    assert write_mock.called
    assert write_mock.call_args.kwargs["cover"] is None


# ---------------------------------------------------------------------------
# Metadata fetch failure (best-effort — should still export)
# ---------------------------------------------------------------------------


def test_process_file_continues_if_fetch_fails(mocker, epub_file, cfg):
    _make_calibre_mocks(mocker, fetch_ok=False)
    export_mock = mocker.patch("bookin.processor.calibredb_export")
    process_file(epub_file, cfg)
    assert export_mock.called
    assert not epub_file.exists()


def test_process_file_continues_if_write_metadata_fails(mocker, epub_file, cfg):
    _make_calibre_mocks(mocker, write_meta_ok=False)
    export_mock = mocker.patch("bookin.processor.calibredb_export")
    process_file(epub_file, cfg)
    assert export_mock.called
    assert not epub_file.exists()


# ---------------------------------------------------------------------------
# Export failure → dead-letter
# ---------------------------------------------------------------------------


def test_process_file_moves_to_failed_on_export_error(mocker, epub_file, cfg, patch_output_dir):
    _make_calibre_mocks(mocker, export_ok=False)
    process_file(epub_file, cfg)

    failed_dir = patch_output_dir / "_failed"
    assert failed_dir.exists()
    assert len(list(failed_dir.glob("*.epub"))) == 1
    assert len(list(failed_dir.glob("*.error"))) == 1


def test_process_file_source_not_deleted_on_failure(mocker, epub_file, cfg):
    _make_calibre_mocks(mocker, export_ok=False)
    process_file(epub_file, cfg)
    assert not epub_file.exists()  # moved to _failed


# ---------------------------------------------------------------------------
# Output placement and collisions
# ---------------------------------------------------------------------------


def test_process_file_places_export_under_output_dir(mocker, epub_file, cfg, output_dir):
    _make_calibre_mocks(mocker)
    process_file(epub_file, cfg)

    exported = output_dir / "Frank Herbert" / "Dune.epub"
    assert exported.exists(), "Template subdirectories must be preserved"
    assert exported.read_bytes() == b"exported epub"


def test_process_file_leaves_no_staging_dir_behind(mocker, epub_file, cfg, output_dir):
    _make_calibre_mocks(mocker)
    process_file(epub_file, cfg)
    assert not list(output_dir.glob(".bookin-staging-*"))


def test_process_file_refuses_to_overwrite_an_existing_export(mocker, epub_file, cfg, output_dir):
    # A second book resolving to the same output path must not clobber the
    # first: that silently destroyed a book, and the source is deleted after a
    # "successful" export, so the loss was unrecoverable.
    existing = output_dir / "Frank Herbert" / "Dune.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"the first book")

    _make_calibre_mocks(mocker)
    process_file(epub_file, cfg)

    assert existing.read_bytes() == b"the first book", "Existing export was overwritten"
    failed = output_dir / "_failed"
    assert len(list(failed.glob("*.epub"))) == 1, "Colliding book should be dead-lettered"


def test_collision_error_names_the_conflicting_path(mocker, epub_file, cfg, output_dir, caplog):
    existing = output_dir / "Frank Herbert" / "Dune.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"the first book")

    _make_calibre_mocks(mocker)
    with caplog.at_level(logging.INFO):
        process_file(epub_file, cfg)

    sidecar = next((output_dir / "_failed").glob("*.error"))
    assert "Dune.epub" in sidecar.read_text()
    assert "already exists" in caplog.text


def test_process_file_fails_when_export_produces_nothing(mocker, epub_file, cfg, output_dir):
    # calibredb exiting 0 without writing anything must not be mistaken for
    # success, or the source file would be deleted with no export to show.
    mocker.patch("bookin.processor.calibredb_add", return_value=1)
    mocker.patch(
        "bookin.processor.read_embedded_metadata",
        return_value={"title": "Dune", "authors": "Frank Herbert", "isbn": ""},
    )
    mocker.patch("bookin.processor.fetch_metadata", return_value=None)
    mocker.patch("bookin.processor.calibredb_export")  # writes no files
    mocker.patch("bookin.processor.calibredb_remove")

    process_file(epub_file, cfg)
    assert len(list((output_dir / "_failed").glob("*.epub"))) == 1


# ---------------------------------------------------------------------------
# Temp dir cleanup
# ---------------------------------------------------------------------------


def test_process_file_cleans_up_temp_dir(mocker, epub_file, cfg):
    created_tmp_dirs = []
    original_mkdtemp = __import__("tempfile").mkdtemp

    def tracking_mkdtemp(**kwargs):
        d = original_mkdtemp(**kwargs)
        created_tmp_dirs.append(Path(d))
        return d

    mocker.patch("bookin.processor.tempfile.mkdtemp", side_effect=tracking_mkdtemp)
    _make_calibre_mocks(mocker)
    process_file(epub_file, cfg)

    for d in created_tmp_dirs:
        assert not d.exists(), f"Temp dir {d} was not cleaned up"
