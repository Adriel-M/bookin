import logging
import urllib.error

import pytest

from bookin.errors import MetadataFetchError
from bookin.hardcover import verify_token


def _resp(mocker, payload: bytes):
    """Build a urlopen() context-manager mock returning ``payload``."""
    resp = mocker.MagicMock()
    resp.read.return_value = payload
    cm = mocker.MagicMock()
    cm.__enter__.return_value = resp
    return cm


def test_verify_token_requires_token():
    with pytest.raises(MetadataFetchError):
        verify_token("")


def test_verify_token_accepts_valid(mocker):
    mocker.patch(
        "urllib.request.urlopen",
        return_value=_resp(mocker, b'{"data": {"me": {"id": 1}}}'),
    )
    verify_token("good-token")  # should not raise


def test_verify_token_rejects_401(mocker):
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
    mocker.patch("urllib.request.urlopen", side_effect=err)
    with pytest.raises(MetadataFetchError):
        verify_token("bad-token")


def test_verify_token_tolerates_unreachable(mocker):
    mocker.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down"))
    verify_token("some-token")  # network failure must not raise


def test_verify_token_sends_bearer_prefix(mocker):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["auth"] = req.get_header("Authorization")
        return _resp(mocker, b'{"data": {"me": {"id": 1}}}')

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    verify_token("raw-token")
    assert captured["auth"] == "Bearer raw-token"


def test_verify_token_never_logs_token(mocker, caplog):
    secret = "hc_secret_value_42"
    mocker.patch(
        "urllib.request.urlopen",
        return_value=_resp(mocker, b'{"data": {"me": {"id": 1}}}'),
    )
    with caplog.at_level(logging.DEBUG):
        verify_token(secret)
    assert secret not in caplog.text
