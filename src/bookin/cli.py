import logging
import os

import click
from rich.logging import RichHandler

from bookin.config import load_config
from bookin.errors import MetadataFetchError
from bookin.hardcover import configure, verify_token
from bookin.version import get_commit
from bookin.watcher import run_daemon


def _setup_logging(level: str) -> None:
    resolved = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=resolved,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    # httpx logs a line per request at INFO, which drowns our own output at the
    # default level. Keep it for --verbose, where it's genuinely useful.
    if resolved > logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.WARNING)


@click.command()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Force DEBUG logging.")
def main(verbose: bool) -> None:
    """Ebook folder watcher and organizer powered by Calibre."""
    cfg = load_config()
    level = "DEBUG" if verbose else cfg.log_level
    _setup_logging(level)

    log = logging.getLogger("bookin")
    log.info("Starting bookin (commit %s)", get_commit())
    log.info("Input: %s  Output: %s", cfg.input_dir.resolve(), cfg.output_dir.resolve())
    log.info("Template: %s", cfg.template)

    # Hardcover is the only metadata source and needs a token. Read it straight
    # from the environment (never stored on Config, so it can't leak via a repr)
    # and fail fast if it's missing or rejected before starting the daemon.
    token = os.environ.get("BOOKIN_HARDCOVER_TOKEN")
    if not token:
        raise click.ClickException(
            "BOOKIN_HARDCOVER_TOKEN is required — Hardcover is the only metadata "
            "source. Get a token at https://hardcover.app/account/api"
        )
    try:
        verify_token(token)
    except MetadataFetchError as err:
        raise click.ClickException(str(err)) from err
    configure(token)

    run_daemon(cfg)
