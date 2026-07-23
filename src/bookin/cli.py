import logging
import os

import click
from rich.logging import RichHandler

from bookin.calibre import configure_hardcover
from bookin.config import load_config
from bookin.version import get_commit
from bookin.watcher import run_daemon


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


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

    # Read the Hardcover token straight from the environment (never stored on
    # Config, so it can't leak via a repr) and hand it to the plugin setup.
    token = os.environ.get("BOOKIN_HARDCOVER_TOKEN")
    if token:
        configure_hardcover(token)
    else:
        log.warning(
            "BOOKIN_HARDCOVER_TOKEN not set — metadata enrichment is disabled; "
            "files will be organized using embedded metadata only"
        )

    run_daemon(cfg)
