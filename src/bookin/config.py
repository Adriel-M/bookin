from dataclasses import dataclass
from pathlib import Path

import yaml

INPUT_DIR = Path("/input")
OUTPUT_DIR = Path("/output")

SUPPORTED_EXTENSIONS = {".epub", ".mobi", ".azw", ".azw3", ".pdf", ".djvu", ".fb2"}
STABILITY_WAIT = 5  # seconds between size checks before processing


@dataclass
class Config:
    template: str = "{authors}/{title}"
    log_level: str = "INFO"


_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as err:
        raise ValueError(f"Invalid YAML in {path}: {err}") from err

    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a YAML mapping, got {type(raw).__name__}")

    known = {k: v for k, v in raw.items() if k in Config.__dataclass_fields__}
    cfg = Config(**known)

    if cfg.log_level.upper() not in _VALID_LOG_LEVELS:
        raise ValueError(f"Invalid log_level {cfg.log_level!r}. Must be one of {_VALID_LOG_LEVELS}")

    return cfg
