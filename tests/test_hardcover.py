import json
import logging

import httpx
import pytest

from bookin import hardcover
from bookin.errors import MetadataFetchError
from bookin.graphql_client.fragments import BookFields
from bookin.hardcover import API_URL, MATCH_THRESHOLD, _author_score, fetch_metadata, verify_token

SECRET_TOKEN = "hc_super_secret_abc123"
COVER_URL = "https://assets.hardcover.app/edition/1/cover.jpg"


# ---------------------------------------------------------------------------
# Harness
#
# Requests are intercepted with httpx.MockTransport rather than by patching the
# generated client, so the real query text, the real Authorization header and
# the real pydantic parsing are all exercised.
# ---------------------------------------------------------------------------


def _install_transport(mocker, handler):
    """Route every httpx.Client built by bookin.hardcover through ``handler``."""
    real_client = httpx.Client

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    mocker.patch("bookin.hardcover.httpx.Client", side_effect=factory)


def _router(operations, cover=None, recorder=None):
    """Build a handler dispatching GraphQL calls by operation name."""

    def handle(request):
        if str(request.url) != API_URL:
            if recorder is not None:
                recorder.append(("cover", None, request))
            return cover(request) if cover else httpx.Response(200, content=b"jpeg-bytes")

        body = json.loads(request.content)
        name = body.get("operationName")
        if recorder is not None:
            recorder.append((name, body.get("variables"), request))

        result = operations[name]
        if callable(result):
            return result(request)
        return httpx.Response(200, json={"data": result})

    return handle


def _configured(mocker, operations, cover=None, recorder=None):
    """Point bookin.hardcover at a mock-transport client, as configure() would."""
    _install_transport(mocker, _router(operations, cover=cover, recorder=recorder))
    client = hardcover._build_client(SECRET_TOKEN)
    mocker.patch.object(hardcover, "_client", client)
    return client


def _author(name, contribution=None):
    return {"contribution": contribution, "author": {"name": name}}


def _edition(
    edition_id=10,
    title="Dune",
    isbn_13="9780441013593",
    isbn_10=None,
    release_date="1965-08-01",
    users_count=100,
    publisher="Chilton Books",
    image=COVER_URL,
):
    return {
        "id": edition_id,
        "title": title,
        "isbn_10": isbn_10,
        "isbn_13": isbn_13,
        "release_date": release_date,
        "users_count": users_count,
        "reading_format_id": 1,
        "publisher": {"name": publisher} if publisher else None,
        "language": {"code3": "eng"},
        "image": {"url": image} if image else None,
    }


def _book(
    book_id=1,
    title="Dune",
    contributions=None,
    series=None,
    position=None,
    featured=True,
    image=None,
    editions=None,
):
    book_series = []
    if series is not None:
        book_series.append({"position": position, "featured": featured, "series": {"name": series}})
    return {
        "id": book_id,
        "title": title,
        "slug": "dune",
        "contributions": (
            contributions if contributions is not None else [_author("Frank Herbert")]
        ),
        "book_series": book_series,
        "image": {"url": image} if image else None,
        "editions": editions if editions is not None else [_edition()],
    }


def _isbn_hit(book=None):
    return {"books": [book if book is not None else _book()]}


def _search_hit(ids=(1,), books=None):
    return {
        "SearchBooks": {"search": {"ids": list(ids)}},
        "FindBooksByIds": {"books": books if books is not None else [_book()]},
    }


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


def test_verify_token_requires_token():
    with pytest.raises(MetadataFetchError):
        verify_token("")


def test_verify_token_accepts_valid(mocker):
    _install_transport(mocker, _router({"VerifyToken": {"me": [{"id": 1}]}}))
    verify_token(SECRET_TOKEN)  # should not raise


@pytest.mark.parametrize("status", [401, 403])
def test_verify_token_rejects_bad_credentials(mocker, status):
    _install_transport(
        mocker,
        _router({"VerifyToken": lambda request: httpx.Response(status, json={"error": "nope"})}),
    )
    with pytest.raises(MetadataFetchError):
        verify_token("bad-token")


def test_verify_token_tolerates_unreachable(mocker):
    def boom(request):
        raise httpx.ConnectError("down")

    _install_transport(mocker, _router({"VerifyToken": boom}))
    verify_token(SECRET_TOKEN)  # network failure must not raise


def test_verify_token_tolerates_server_error(mocker):
    _install_transport(
        mocker, _router({"VerifyToken": lambda request: httpx.Response(500, text="boom")})
    )
    verify_token(SECRET_TOKEN)  # a 5xx must not block startup


def test_verify_token_sends_bearer_prefix(mocker):
    seen = []
    _install_transport(mocker, _router({"VerifyToken": {"me": [{"id": 1}]}}, recorder=seen))
    verify_token("raw-token")
    assert seen[0][2].headers["Authorization"] == "Bearer raw-token"


def test_verify_token_keeps_an_existing_scheme(mocker):
    seen = []
    _install_transport(mocker, _router({"VerifyToken": {"me": [{"id": 1}]}}, recorder=seen))
    verify_token("Bearer already-prefixed")
    assert seen[0][2].headers["Authorization"] == "Bearer already-prefixed"


def test_verify_token_never_logs_token(mocker, caplog):
    _install_transport(mocker, _router({"VerifyToken": {"me": [{"id": 1}]}}))
    with caplog.at_level(logging.DEBUG):
        verify_token(SECRET_TOKEN)
    assert SECRET_TOKEN not in caplog.text


# ---------------------------------------------------------------------------
# fetch_metadata — lookup paths
# ---------------------------------------------------------------------------


def test_fetch_metadata_returns_none_when_unconfigured(mocker):
    mocker.patch.object(hardcover, "_client", None)
    assert fetch_metadata("Dune", "Frank Herbert", None) is None


def test_fetch_metadata_uses_isbn_when_available(mocker):
    seen = []
    _configured(mocker, {"FindBookByIsbn": _isbn_hit()}, recorder=seen)
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert [call[0] for call in seen] == ["FindBookByIsbn"]
    assert seen[0][1] == {"isbn": "9780441013593"}


@pytest.mark.parametrize(
    "supplied",
    ["978-0-441-01359-3", "978 0 441 01359 3", "ISBN 9780441013593", "9780441013593"],
)
def test_fetch_metadata_normalizes_the_isbn(mocker, supplied):
    # Embedded metadata often carries the hyphenated form, but Hardcover stores
    # isbn_13 unhyphenated — querying the raw value matches nothing.
    seen = []
    _configured(mocker, {"FindBookByIsbn": _isbn_hit()}, recorder=seen)
    fetch_metadata("Dune", "Frank Herbert", supplied)
    assert seen[0][1] == {"isbn": "9780441013593"}


def test_fetch_metadata_keeps_an_isbn10_check_digit(mocker):
    seen = []
    _configured(mocker, {"FindBookByIsbn": _isbn_hit()}, recorder=seen)
    fetch_metadata("Dune", "Frank Herbert", "0-441-01359-X")
    assert seen[0][1] == {"isbn": "044101359X"}


def test_fetch_metadata_ignores_an_isbn_with_no_digits(mocker):
    seen = []
    _configured(mocker, _search_hit(), recorder=seen)
    fetch_metadata("Dune", "Frank Herbert", "n/a")
    assert [call[0] for call in seen] == ["SearchBooks", "FindBooksByIds"]


def test_fetch_metadata_falls_back_to_search_when_isbn_misses(mocker):
    seen = []
    _configured(
        mocker,
        {"FindBookByIsbn": {"books": []}, **_search_hit()},
        recorder=seen,
    )
    meta = fetch_metadata("Dune", "Frank Herbert", "9999999999999")
    assert meta is not None
    assert [call[0] for call in seen] == ["FindBookByIsbn", "SearchBooks", "FindBooksByIds"]


def test_fetch_metadata_search_query_includes_author(mocker):
    seen = []
    _configured(mocker, _search_hit(), recorder=seen)
    fetch_metadata("Dune", "Frank Herbert", None)
    assert seen[0][1] == {"query": "Dune Frank Herbert"}


def test_fetch_metadata_returns_none_with_no_search_terms(mocker):
    _configured(mocker, {})
    assert fetch_metadata(None, None, None) is None


def test_fetch_metadata_returns_none_when_search_finds_nothing(mocker):
    _configured(mocker, {"SearchBooks": {"search": {"ids": []}}})
    assert fetch_metadata("Nonexistent Book", None, None) is None


def test_fetch_metadata_restores_search_relevance_order(mocker):
    # Hasura loses _in ordering, so FindBooksByIds returns them reversed here.
    # The best-relevance id (7) must still win despite equal title scores.
    books = [
        _book(book_id=3, title="Dune", editions=[_edition(edition_id=30, publisher="Wrong")]),
        _book(book_id=7, title="Dune", editions=[_edition(edition_id=70, publisher="Right")]),
    ]
    _configured(
        mocker,
        {
            "SearchBooks": {"search": {"ids": [7, 3]}},
            "FindBooksByIds": {"books": books},
        },
    )
    meta = fetch_metadata("Dune", "Frank Herbert", None)
    assert meta is not None
    assert meta["publisher"] == "Right"


def test_fetch_metadata_picks_the_most_owned_edition(mocker):
    book = _book(
        editions=[
            _edition(edition_id=1, users_count=5, publisher="Rare"),
            _edition(edition_id=2, users_count=900, publisher="Popular"),
            _edition(edition_id=3, users_count=50, publisher="Middling"),
        ]
    )
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert meta["publisher"] == "Popular"


# ---------------------------------------------------------------------------
# fetch_metadata — ranking
# ---------------------------------------------------------------------------


def test_fetch_metadata_rejects_a_title_that_does_not_match(mocker):
    _configured(mocker, _search_hit(books=[_book(title="Something Entirely Different")]))
    assert fetch_metadata("Dune", "Frank Herbert", None) is None


def test_fetch_metadata_rejects_a_wrong_author(mocker):
    _configured(mocker, _search_hit(books=[_book(contributions=[_author("Brandon Sanderson")])]))
    assert fetch_metadata("Dune", "Frank Herbert", None) is None


def test_fetch_metadata_rejects_a_wrong_sole_author(mocker):
    # Regression guard. The plugin averages weighted author scores over the
    # contributor count, so a single contributor weighted 2.0 scores ~1.2 even
    # when completely wrong and clears the threshold. We divide by total weight
    # instead, keeping the score in [0, 1] and the threshold meaningful.
    _configured(mocker, _search_hit(books=[_book(contributions=[_author("Brandon Sanderson")])]))
    assert fetch_metadata("Dune", "Frank Herbert", None) is None


def test_fetch_metadata_author_score_stays_bounded():
    contributions = BookFields.model_validate(_book()).contributions
    exact = _author_score(contributions, ["Frank Herbert"])
    wrong = _author_score(contributions, ["Brandon Sanderson"])

    assert exact == pytest.approx(1.0)
    assert wrong < MATCH_THRESHOLD


def test_fetch_metadata_matches_regardless_of_title_case(mocker):
    _configured(mocker, _search_hit(books=[_book(title="DUNE")]))
    assert fetch_metadata("dune", "Frank Herbert", None) is not None


def test_fetch_metadata_matches_when_no_authors_are_known(mocker):
    _configured(mocker, _search_hit(books=[_book(contributions=[_author("Someone Else")])]))
    # With no author to compare against, title similarity alone decides.
    assert fetch_metadata("Dune", None, None) is not None


def test_fetch_metadata_tolerates_a_translator_on_the_right_book(mocker):
    book = _book(
        contributions=[_author("Frank Herbert"), _author("A Translator", "Translator")],
    )
    _configured(mocker, _search_hit(books=[book]))
    assert fetch_metadata("Dune", "Frank Herbert", None) is not None


def test_fetch_metadata_skips_books_with_no_usable_edition(mocker):
    _configured(mocker, _search_hit(books=[_book(editions=[])]))
    assert fetch_metadata("Dune", "Frank Herbert", None) is None


# ---------------------------------------------------------------------------
# fetch_metadata — field mapping
# ---------------------------------------------------------------------------


def test_fetch_metadata_maps_every_field(mocker):
    book = _book(series="Dune", position=1.0)
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta == {
        "title": "Dune",
        "authors": "Frank Herbert",
        "series": "Dune",
        "series_index": "1",
        "publisher": "Chilton Books",
        "pubdate": "1965-08-01",
        "isbn": "9780441013593",
    }


def test_fetch_metadata_joins_multiple_authors(mocker):
    # The old OPF path read dc:creator singular, so only the first author ever
    # survived. All primary authors must come through now.
    book = _book(contributions=[_author("Frank Herbert"), _author("Brian Herbert")])
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert meta["authors"] == "Frank Herbert & Brian Herbert"


def test_fetch_metadata_excludes_non_author_contributors(mocker):
    book = _book(
        contributions=[
            _author("Frank Herbert"),
            _author("Some Translator", "Translator"),
            _author("Some Illustrator", "Illustrator"),
        ]
    )
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert meta["authors"] == "Frank Herbert"


def test_fetch_metadata_falls_back_to_all_contributors(mocker):
    book = _book(contributions=[_author("Only A Translator", "Translator")])
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", None, "9780441013593")
    assert meta is not None
    assert meta["authors"] == "Only A Translator"


def test_fetch_metadata_keeps_series_index_zero(mocker):
    # Position 0 is a real index (The Hobbit sits at 0 in its series). The
    # Calibre plugin tests truthiness here and drops it.
    book = _book(series="Middle-earth", position=0.0)
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert meta["series_index"] == "0"


def test_fetch_metadata_keeps_fractional_series_index(mocker):
    book = _book(series="Dune", position=1.5)
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert meta["series_index"] == "1.5"


def test_fetch_metadata_prefers_the_featured_series(mocker):
    book = _book()
    book["book_series"] = [
        {"position": 9.0, "featured": False, "series": {"name": "Some Omnibus"}},
        {"position": 1.0, "featured": True, "series": {"name": "Dune"}},
    ]
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert meta["series"] == "Dune"
    assert meta["series_index"] == "1"


def test_fetch_metadata_omits_absent_fields(mocker):
    book = _book(editions=[_edition(publisher=None, release_date=None, isbn_13=None)])
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")
    assert meta is not None
    assert "publisher" not in meta
    assert "pubdate" not in meta
    assert "isbn" not in meta
    assert "series" not in meta


def test_fetch_metadata_falls_back_to_isbn_10(mocker):
    book = _book(editions=[_edition(isbn_13=None, isbn_10="0441013597")])
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    meta = fetch_metadata("Dune", "Frank Herbert", "0441013597")
    assert meta is not None
    assert meta["isbn"] == "0441013597"


# ---------------------------------------------------------------------------
# fetch_metadata — covers
# ---------------------------------------------------------------------------


def test_fetch_metadata_downloads_the_cover(mocker, tmp_path):
    _configured(mocker, {"FindBookByIsbn": _isbn_hit()})
    cover = tmp_path / "cover.jpg"
    fetch_metadata("Dune", "Frank Herbert", "9780441013593", cover)
    assert cover.read_bytes() == b"jpeg-bytes"


def test_fetch_metadata_cover_request_omits_the_token(mocker, tmp_path):
    # The asset host does not need our credentials and must never see them.
    seen = []
    _configured(mocker, {"FindBookByIsbn": _isbn_hit()}, recorder=seen)
    fetch_metadata("Dune", "Frank Herbert", "9780441013593", tmp_path / "cover.jpg")
    cover_request = next(request for name, _, request in seen if name == "cover")
    assert "Authorization" not in cover_request.headers


def test_fetch_metadata_falls_back_to_the_book_cover(mocker, tmp_path):
    seen = []
    book = _book(image="https://assets.hardcover.app/book.jpg", editions=[_edition(image=None)])
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)}, recorder=seen)
    fetch_metadata("Dune", "Frank Herbert", "9780441013593", tmp_path / "cover.jpg")
    cover_request = next(request for name, _, request in seen if name == "cover")
    assert str(cover_request.url) == "https://assets.hardcover.app/book.jpg"


def test_fetch_metadata_survives_a_failed_cover_download(mocker, tmp_path):
    _configured(
        mocker,
        {"FindBookByIsbn": _isbn_hit()},
        cover=lambda request: httpx.Response(404),
    )
    cover = tmp_path / "cover.jpg"
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593", cover)
    assert meta is not None, "A missing cover must not lose the metadata"
    assert not cover.exists()


def test_fetch_metadata_skips_cover_when_none_is_available(mocker, tmp_path):
    book = _book(image=None, editions=[_edition(image=None)])
    _configured(mocker, {"FindBookByIsbn": _isbn_hit(book)})
    cover = tmp_path / "cover.jpg"
    assert fetch_metadata("Dune", "Frank Herbert", "9780441013593", cover) is not None
    assert not cover.exists()


# ---------------------------------------------------------------------------
# fetch_metadata — failure handling
# ---------------------------------------------------------------------------


def test_fetch_metadata_returns_none_on_graphql_errors(mocker):
    # Hasura answers 200 with {"errors": [...]} and no data. The Calibre plugin
    # mistakes this for an empty result set; it has to surface as a failure.
    _configured(
        mocker,
        {
            "FindBookByIsbn": lambda request: httpx.Response(
                200, json={"errors": [{"message": "field 'books' not found"}]}
            )
        },
    )
    assert fetch_metadata("Dune", "Frank Herbert", "9780441013593") is None


def test_fetch_metadata_returns_none_on_http_error(mocker):
    _configured(
        mocker, {"FindBookByIsbn": lambda request: httpx.Response(500, text="server error")}
    )
    assert fetch_metadata("Dune", "Frank Herbert", "9780441013593") is None


def test_fetch_metadata_returns_none_when_unreachable(mocker):
    def boom(request):
        raise httpx.ConnectError("down")

    _configured(mocker, {"FindBookByIsbn": boom})
    assert fetch_metadata("Dune", "Frank Herbert", "9780441013593") is None


def test_fetch_metadata_returns_none_on_malformed_response(mocker):
    _configured(mocker, {"FindBookByIsbn": lambda request: httpx.Response(200, text="not json")})
    assert fetch_metadata("Dune", "Frank Herbert", "9780441013593") is None


def test_fetch_metadata_retries_once_after_a_rate_limit(mocker):
    sleep = mocker.patch("bookin.hardcover.time.sleep")
    attempts = []

    def throttled_then_ok(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, json={"error": "Throttled"}, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"data": _isbn_hit()})

    _configured(mocker, {"FindBookByIsbn": throttled_then_ok})
    meta = fetch_metadata("Dune", "Frank Herbert", "9780441013593")

    assert meta is not None, "A 429 should be retried, not dropped"
    assert len(attempts) == 2
    sleep.assert_called_once_with(2.0)


def test_fetch_metadata_gives_up_after_a_second_rate_limit(mocker):
    mocker.patch("bookin.hardcover.time.sleep")
    _configured(
        mocker,
        {"FindBookByIsbn": lambda request: httpx.Response(429, json={"error": "Throttled"})},
    )
    assert fetch_metadata("Dune", "Frank Herbert", "9780441013593") is None


def test_fetch_metadata_never_logs_token(mocker, caplog, tmp_path):
    _configured(mocker, {"FindBookByIsbn": _isbn_hit()})
    with caplog.at_level(logging.DEBUG):
        fetch_metadata("Dune", "Frank Herbert", "9780441013593", tmp_path / "cover.jpg")
    assert SECRET_TOKEN not in caplog.text


def test_configure_does_not_log_token(mocker, caplog):
    _install_transport(mocker, _router({}))
    with caplog.at_level(logging.DEBUG):
        hardcover.configure(SECRET_TOKEN)
    assert SECRET_TOKEN not in caplog.text
