"""Direct Hardcover API access — used only to validate the token at startup.

All book metadata is fetched via Calibre's Hardcover plugin (see calibre.py).
This module makes the single lightweight authenticated call the plugin can't
give us up front: confirming the API token is present and accepted.
"""

import json
import logging
import urllib.error
import urllib.request

from bookin.errors import MetadataFetchError

log = logging.getLogger("bookin.hardcover")

API_URL = "https://api.hardcover.app/v1/graphql"
_VERIFY_QUERY = "query { me { id } }"


def verify_token(token: str, timeout: int = 15) -> None:
    """Validate the Hardcover API token with a minimal authenticated query.

    Raises MetadataFetchError if the token is missing or rejected (HTTP
    401/403). If Hardcover can't be reached (network error or 5xx), logs a
    warning and returns without failing — a transient outage shouldn't block
    startup, and every metadata fetch is best-effort anyway.

    The token is sent only in the Authorization header; it is never logged.
    """
    if not token:
        raise MetadataFetchError("BOOKIN_HARDCOVER_TOKEN is not set")

    # Match the plugin's header handling: add the Bearer prefix unless the
    # supplied token already carries one.
    authorization = token if " " in token else f"Bearer {token}"
    body = json.dumps({"query": _VERIFY_QUERY}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": authorization},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise MetadataFetchError(
                f"Hardcover rejected the API token (HTTP {err.code}). "
                "Check BOOKIN_HARDCOVER_TOKEN — get one at https://hardcover.app/account/api"
            ) from err
        log.warning("Could not validate Hardcover token (HTTP %d) — continuing", err.code)
        return
    except (urllib.error.URLError, TimeoutError) as err:
        log.warning("Could not reach Hardcover to validate token — continuing (%s)", err)
        return

    # An invalid token surfaces above as HTTP 401, so reaching a data payload
    # means auth succeeded. A 200 without data is more likely a benign schema
    # change than an auth failure, so warn rather than block startup.
    if isinstance(payload, dict) and "data" in payload:
        log.info("Hardcover API token validated")
    else:
        log.warning("Unexpected response validating Hardcover token — continuing")
