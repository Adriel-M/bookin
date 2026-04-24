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


def _make_calibre_mocks(mocker, *, fetch_ok=True, write_meta_ok=True, export_ok=True):
    mocker.patch("bookin.processor.calibredb_add", return_value=1)
    mocker.patch(
        "bookin.processor.read_embedded_metadata",
        return_value={"title": "Dune", "authors": "Frank Herbert", "isbn": ""},
    )
    mocker.patch(
        "bookin.processor.fetch_metadata",
        return_value="<opf/>" if fetch_ok else None,
    )
    mocker.patch(
        "bookin.processor.parse_opf",
        return_value={"title": "Dune", "authors": "Frank Herbert"},
    )
    mocker.patch(
        "bookin.processor.write_metadata",
        side_effect=None if write_meta_ok else CalibreCommandError("write failed"),
    )
    mocker.patch(
        "bookin.processor.calibredb_export",
        side_effect=None if export_ok else CalibreCommandError("export failed"),
    )
    mocker.patch("bookin.processor.calibredb_remove")


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
