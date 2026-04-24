FROM debian:trixie-slim

# Install Calibre from apt (handles all dependencies automatically).
RUN apt-get update && apt-get install -y --no-install-recommends \
    calibre \
    && rm -rf /var/lib/apt/lists/*

# Tell Qt to use the offscreen platform — no X11 display needed.
ENV QT_QPA_PLATFORM=offscreen

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

ENTRYPOINT ["/app/.venv/bin/bookin"]
