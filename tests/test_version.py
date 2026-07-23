import subprocess

from bookin.version import get_commit


def test_get_commit_prefers_env(monkeypatch):
    get_commit.cache_clear()
    monkeypatch.setenv("BOOKIN_COMMIT", "deadbeef")
    assert get_commit() == "deadbeef"
    get_commit.cache_clear()


def test_get_commit_falls_back_to_git(monkeypatch, mocker):
    get_commit.cache_clear()
    monkeypatch.delenv("BOOKIN_COMMIT", raising=False)
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "abc1234\n", ""),
    )
    assert get_commit() == "abc1234"
    get_commit.cache_clear()


def test_get_commit_unknown_without_git(monkeypatch, mocker):
    get_commit.cache_clear()
    monkeypatch.delenv("BOOKIN_COMMIT", raising=False)
    mocker.patch("subprocess.run", side_effect=FileNotFoundError)
    assert get_commit() == "unknown"
    get_commit.cache_clear()
