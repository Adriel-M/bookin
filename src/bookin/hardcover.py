"""Hardcover metadata client.

Talks to Hardcover's GraphQL API directly through the typed client generated
from the vendored schema (see ``src/bookin/graphql_client``, regenerate with
``make codegen``). Calibre is still used to embed and export, but it is no
longer the metadata source.

The API token is supplied once via ``configure`` and lives only in the HTTP
client's Authorization header — it is never logged, never written to disk, and
never passed on a command line.
"""

import logging
import re
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import ValidationError
from pyjarowinkler.distance import get_jaro_winkler_similarity

from bookin.errors import MetadataFetchError
from bookin.graphql_client import HardcoverClient
from bookin.graphql_client.exceptions import GraphQLClientError, GraphQLClientHttpError
from bookin.graphql_client.find_book_by_isbn import FindBookByIsbnBooks
from bookin.graphql_client.find_books_by_ids import FindBooksByIdsBooks
from bookin.graphql_client.fragments import BookFields, BookFieldsContributions, EditionFields

# The generated BookFields fragment carries everything except `editions`, which
# each operation selects with its own filters. These are the two concrete book
# shapes our queries return.
BookWithEditions = FindBookByIsbnBooks | FindBooksByIdsBooks

log = logging.getLogger("bookin.hardcover")

API_URL = "https://api.hardcover.app/v1/graphql"
# Hardcover's docs ask API consumers to identify themselves.
USER_AGENT = "bookin (+https://github.com/Adriel-M/bookin)"

DEFAULT_TIMEOUT = 30  # Hardcover caps query execution at 30s
COVER_TIMEOUT = 30

# Editions are restricted to these languages; editions with no language
# recorded are kept too, so untagged ones aren't silently dropped.
DEFAULT_LANGUAGES = ["eng"]

# Minimum Jaro-Winkler similarity for a candidate to be considered a match.
# Matches the Calibre plugin's default match_sensitivity.
MATCH_THRESHOLD = 0.7
# Primary authors count double against translators, illustrators and the like.
AUTHOR_CONTRIBUTION_WEIGHT = 2.0
# pyjarowinkler rounds to 2 decimals by default, which is too coarse to compare
# against the threshold meaningfully.
_SIMILARITY_DECIMALS = 10

# Set once at startup by configure(). Holding a single client keeps the
# connection pool warm across the daemon's lifetime.
_client: HardcoverClient | None = None


def _auth_header(token: str) -> str:
    """Build the Authorization value.

    A token containing a space is assumed to already carry its scheme, so the
    Bearer prefix is only added when it's absent.
    """
    return token if " " in token else f"Bearer {token}"


def _build_client(token: str, timeout: int = DEFAULT_TIMEOUT) -> HardcoverClient:
    http_client = httpx.Client(
        headers={"Authorization": _auth_header(token), "User-Agent": USER_AGENT},
        timeout=timeout,
    )
    return HardcoverClient(url=API_URL, http_client=http_client)


def configure(token: str) -> None:
    """Build the shared Hardcover client. Call once, at startup."""
    global _client
    _client = _build_client(token)
    log.info("Hardcover metadata client configured")


def verify_token(token: str, timeout: int = 15) -> None:
    """Validate the Hardcover API token with a minimal authenticated query.

    Raises MetadataFetchError if the token is missing or rejected (HTTP
    401/403). If Hardcover can't be reached (network error or 5xx), logs a
    warning and returns without failing — a transient outage shouldn't block
    startup, and every metadata fetch is best-effort anyway.
    """
    if not token:
        raise MetadataFetchError("BOOKIN_HARDCOVER_TOKEN is not set")

    try:
        with _build_client(token, timeout=timeout) as client:
            client.verify_token()
    except GraphQLClientHttpError as err:
        if err.status_code in (401, 403):
            raise MetadataFetchError(
                f"Hardcover rejected the API token (HTTP {err.status_code}). "
                "Check BOOKIN_HARDCOVER_TOKEN — get one at https://hardcover.app/account/api"
            ) from err
        log.warning("Could not validate Hardcover token (HTTP %d) — continuing", err.status_code)
        return
    except (GraphQLClientError, httpx.HTTPError, ValidationError) as err:
        log.warning("Could not reach Hardcover to validate token — continuing (%s)", err)
        return

    log.info("Hardcover API token validated")


# ---- matching ----


def _similarity(first: str, second: str, scaling: float = 0.1, norm_case: bool = False) -> float:
    if not first or not second:
        return 0.0
    return float(
        get_jaro_winkler_similarity(
            first, second, scaling=scaling, decimals=_SIMILARITY_DECIMALS, norm_case=norm_case
        )
    )


def _normalize_isbn(isbn: str) -> str:
    """Strip formatting from an ISBN.

    Embedded metadata often carries the hyphenated form ("978-0-307-49846-5"),
    but Hardcover stores isbn_10/isbn_13 unhyphenated, so an `_eq` match on the
    raw value silently finds nothing. The ISBN-10 check digit may be 'X'.
    """
    return re.sub(r"[^0-9Xx]", "", isbn).upper()


def _split_authors(authors: str | None) -> list[str]:
    """Split an embedded author string into individual names.

    Calibre joins authors with ``&``; some files use commas instead.
    """
    if not authors:
        return []
    separator = "&" if "&" in authors else ","
    return [part.strip() for part in authors.split(separator) if part.strip()]


def _author_score(contributions: Sequence[BookFieldsContributions], authors: list[str]) -> float:
    """Score a book's contributors against the authors we read off the file.

    Each contributor is scored by its best match against any supplied author,
    weighted so primary authors dominate, then averaged over all contributors —
    so a book credited to the right author scores high even if the edition also
    lists a translator.

    The average is taken over the total *weight*, not the contributor count.
    The Calibre plugin divides by count, which lets a weight of 2.0 push a
    single contributor's score above 1.0 — a completely wrong sole author
    scores ~1.2 there and clears any threshold below that, so the check never
    actually rejects anything for single-author books.
    """
    total = 0.0
    total_weight = 0.0
    for contribution in contributions:
        if contribution.author is None:
            continue
        weight = (
            AUTHOR_CONTRIBUTION_WEIGHT if contribution.contribution in (None, "Author") else 1.0
        )
        best = max(
            _similarity(author, contribution.author.name, scaling=0.0, norm_case=True)
            for author in authors
        )
        total += best * weight
        total_weight += weight
    return total / total_weight if total_weight else 0.0


def _restore_search_order(
    books: Sequence[FindBooksByIdsBooks], ids: list[int]
) -> list[FindBooksByIdsBooks]:
    """Put hydrated books back into search-relevance order.

    Hasura does not preserve the ordering of an ``_in`` filter, so the ranking
    Hardcover's search gave us would otherwise be lost.
    """
    position = {book_id: index for index, book_id in enumerate(ids)}
    return sorted(books, key=lambda book: position.get(book.id, len(ids)))


def _best_edition(editions: Sequence[EditionFields]) -> EditionFields | None:
    """Pick the most widely owned edition."""
    if not editions:
        return None
    return max(editions, key=lambda edition: edition.users_count)


def _pick_book(
    books: Sequence[FindBooksByIdsBooks],
    title: str,
    authors: list[str],
) -> FindBooksByIdsBooks | None:
    """Choose the best-matching book, or None if nothing clears the threshold."""
    scored: list[tuple[float, FindBooksByIdsBooks]] = []
    for book in books:
        if not book.title:
            continue
        # Case-insensitive: embedded titles are inconsistently cased, and the
        # plugin's case-sensitive comparison rejects "DUNE" against "Dune".
        title_score = _similarity(title, book.title, norm_case=True)
        if title_score < MATCH_THRESHOLD:
            continue
        if authors and _author_score(book.contributions, authors) < MATCH_THRESHOLD:
            continue
        if not book.editions:
            continue
        scored.append((title_score, book))

    if not scored:
        return None
    # Stable sort, so books tying on title score keep search-relevance order.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


# ---- field mapping ----


def _author_names(contributions: Sequence[BookFieldsContributions]) -> list[str]:
    """Primary author names, de-duplicated, in the order Hardcover returned them.

    Falls back to every contributor if none is explicitly credited as an author.
    """
    primary: list[str] = []
    everyone: list[str] = []
    for contribution in contributions:
        if contribution.author is None:
            continue
        name = contribution.author.name.strip()
        if not name:
            continue
        if name not in everyone:
            everyone.append(name)
        if contribution.contribution in (None, "Author") and name not in primary:
            primary.append(name)
    return primary or everyone


def _format_series_index(position: float) -> str:
    """Render a series position, dropping the decimal when it's a whole number."""
    return str(int(position)) if position.is_integer() else str(position)


def _build_metadata(book: BookFields, edition: EditionFields) -> dict[str, str]:
    meta: dict[str, str] = {}

    title = edition.title or book.title
    if title:
        meta["title"] = title

    authors = _author_names(book.contributions)
    if authors:
        # Calibre treats "&" as its author separator.
        meta["authors"] = " & ".join(authors)

    # Prefer the series Hardcover marks as featured.
    series_entries = [entry for entry in book.book_series if entry.series]
    featured = next((entry for entry in series_entries if entry.featured), None)
    entry = featured or (series_entries[0] if series_entries else None)
    if entry is not None and entry.series is not None:
        meta["series"] = entry.series.name
        # Explicit None check: position 0 is a real series index.
        if entry.position is not None:
            meta["series_index"] = _format_series_index(entry.position)

    if edition.publisher and edition.publisher.name:
        meta["publisher"] = edition.publisher.name
    if edition.release_date:
        meta["pubdate"] = edition.release_date

    isbn = edition.isbn_13 or edition.isbn_10
    if isbn:
        meta["isbn"] = isbn

    return meta


def _cover_url(book: BookFields, edition: EditionFields) -> str | None:
    if edition.image and edition.image.url:
        return edition.image.url
    if book.image and book.image.url:
        return book.image.url
    return None


def _download_cover(url: str, dest: Path) -> None:
    """Fetch a cover image.

    Deliberately does not reuse the API client: its Authorization header would
    send our token to the asset host, which does not need it.
    """
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=COVER_TIMEOUT, follow_redirects=True
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)
    log.debug("Downloaded cover to %s", dest.name)


# ---- lookup ----


def _retry_after(response: httpx.Response, default: float = 5.0) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _find_match(
    client: HardcoverClient,
    title: str | None,
    authors: list[str],
    isbn: str | None,
) -> tuple[BookWithEditions, EditionFields] | None:
    """Resolve a book, by ISBN if we have one, otherwise by title/author search."""
    normalized_isbn = _normalize_isbn(isbn) if isbn else ""
    if normalized_isbn:
        log.info("Querying Hardcover: isbn=%r", normalized_isbn)
        books = client.find_book_by_isbn(isbn=normalized_isbn).books
        if books:
            edition = _best_edition(books[0].editions)
            if edition is not None:
                return books[0], edition
        log.info("No Hardcover match for isbn=%r — trying a title search", normalized_isbn)

    if not title:
        log.warning("No title, author, or ISBN — skipping metadata fetch")
        return None

    query = f"{title} {authors[0]}" if authors else title
    log.info("Querying Hardcover: %r", query)
    search = client.search_books(query=query)
    ids = [i for i in (search.search.ids or []) if i is not None] if search.search else []
    if not ids:
        log.warning("Hardcover search returned no results for %r", query)
        return None

    hydrated = client.find_books_by_ids(ids=ids, languages=DEFAULT_LANGUAGES)
    book = _pick_book(_restore_search_order(hydrated.books, ids), title, authors)
    if book is None:
        log.warning("No Hardcover result cleared the match threshold for %r", query)
        return None

    edition = _best_edition(book.editions)
    if edition is None:
        return None
    return book, edition


def fetch_metadata(
    title: str | None,
    authors: str | None,
    isbn: str | None,
    cover_path: Path | None = None,
) -> dict[str, str] | None:
    """Look up a book on Hardcover. Returns metadata fields, or None if unmatched.

    If ``cover_path`` is given, the cover image (when found) is downloaded to
    that path.

    Enrichment is best-effort: this never raises. Any failure is logged and
    returns None, leaving the file to be exported on its embedded metadata.
    """
    if _client is None:
        log.warning("Hardcover client is not configured — skipping metadata fetch")
        return None

    author_list = _split_authors(authors)
    try:
        try:
            match = _find_match(_client, title, author_list, isbn)
        except GraphQLClientHttpError as err:
            if err.status_code != 429:
                raise
            delay = _retry_after(err.response)
            log.warning("Hardcover rate limit reached — retrying in %.1fs", delay)
            time.sleep(delay)
            match = _find_match(_client, title, author_list, isbn)
    except GraphQLClientHttpError as err:
        log.warning("Hardcover lookup failed (HTTP %d)", err.status_code)
        return None
    except (GraphQLClientError, httpx.HTTPError, ValidationError) as err:
        log.warning("Hardcover lookup failed: %s", err)
        return None

    if match is None:
        return None
    book, edition = match

    meta = _build_metadata(book, edition)
    log.info("Hardcover matched: %s", meta.get("title", "<untitled>"))
    log.debug("Metadata: %s", meta)

    if cover_path is not None:
        url = _cover_url(book, edition)
        if url:
            try:
                _download_cover(url, cover_path)
            except (httpx.HTTPError, OSError) as err:
                log.warning("Could not download cover (continuing without): %s", err)

    return meta
