import os
from dataclasses import dataclass
from pathlib import Path

INPUT_DIR = Path("/input")
OUTPUT_DIR = Path("/output")

SUPPORTED_EXTENSIONS = {".epub", ".mobi", ".azw", ".azw3", ".pdf", ".djvu", ".fb2"}
STABILITY_WAIT = 5  # seconds between size checks before processing

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_DEFAULT_TEMPLATE = (
    'program: if field("series") then field("series") & "/" &'
    ' format_number(field("series_index"), "02.0f") & " - " & field("title") & " - " &'
    ' field("authors") else field("title") & " - " & field("authors") fi'
)


@dataclass
class Config:
    template: str = _DEFAULT_TEMPLATE
    log_level: str = "INFO"


def load_config() -> Config:
    template = os.environ.get("BOOKIN_TEMPLATE", _DEFAULT_TEMPLATE)
    log_level = os.environ.get("BOOKIN_LOG_LEVEL", "INFO")

    if log_level.upper() not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"Invalid BOOKIN_LOG_LEVEL {log_level!r}. Must be one of {_VALID_LOG_LEVELS}"
        )

    return Config(template=template, log_level=log_level)
