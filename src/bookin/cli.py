import logging

import click
from rich.logging import RichHandler

from bookin.config import load_config
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
    log.info("Input: %s  Output: %s", cfg.input_dir.resolve(), cfg.output_dir.resolve())
    log.info("Template: %s", cfg.template)

    run_daemon(cfg)
