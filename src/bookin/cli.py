import logging
from pathlib import Path

import click
from rich.logging import RichHandler

from bookin.config import INPUT_DIR, OUTPUT_DIR, load_config
from bookin.watcher import run_daemon


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


@click.command()
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Force DEBUG logging.")
def main(config_path: Path, verbose: bool) -> None:
    """Ebook folder watcher and organizer powered by Calibre."""
    cfg = load_config(config_path)
    level = "DEBUG" if verbose else cfg.log_level
    _setup_logging(level)

    log = logging.getLogger("bookin")
    log.info("Input: %s  Output: %s", INPUT_DIR, OUTPUT_DIR)
    log.info("Template: %s", cfg.template)

    run_daemon(cfg)
