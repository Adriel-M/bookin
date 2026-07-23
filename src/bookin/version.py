"""Resolve the running build's commit hash for startup logging."""

import os
import subprocess
from functools import cache
from pathlib import Path

_ENV_VAR = "BOOKIN_COMMIT"


@cache
def get_commit() -> str:
    """Return the commit this build was made from.

    Prefers the ``BOOKIN_COMMIT`` env var, which is baked into the Docker
    image at build time (there is no ``.git`` inside the container). Falls
    back to ``git rev-parse`` for local runs from a checkout, and finally to
    ``"unknown"`` when neither is available.
    """
    env_commit = os.environ.get(_ENV_VAR, "").strip()
    if env_commit:
        return env_commit

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"

    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"
