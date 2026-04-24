import os
from dataclasses import dataclass
from pathlib import Path

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
    input_dir: Path = Path("/input")
    output_dir: Path = Path("/output")


def load_config() -> Config:
    template = os.environ.get("BOOKIN_TEMPLATE", _DEFAULT_TEMPLATE)
    log_level = os.environ.get("BOOKIN_LOG_LEVEL", "INFO")
    input_dir = Path(os.environ.get("BOOKIN_INPUT_DIR", "/input"))
    output_dir = Path(os.environ.get("BOOKIN_OUTPUT_DIR", "/output"))

    if log_level.upper() not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"Invalid BOOKIN_LOG_LEVEL {log_level!r}. Must be one of {_VALID_LOG_LEVELS}"
        )

    return Config(
        template=template, log_level=log_level, input_dir=input_dir, output_dir=output_dir
    )
