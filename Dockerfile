# Calibre (CLI tools: calibredb, ebook-meta, fetch-ebook-metadata) comes
# pre-installed and stays close to upstream — no apt install of Calibre here.
FROM lscr.io/linuxserver/calibre:latest

# Calibre's CLI tools use Qt internally; run them headless without a display.
ENV QT_QPA_PLATFORM=offscreen
# Give the Calibre tools a writable HOME for their config/cache.
ENV HOME=/tmp

# uv manages its own standalone Python, so we don't depend on the base image's
# interpreter. This keeps the app env self-contained across base-image updates.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Stage the third-party Hardcover metadata plugin (installed into a private
# Calibre config dir at startup by configure_hardcover, not here). Bump the
# version to update; the plugin tracks Hardcover's beta API.
ARG HARDCOVER_PLUGIN_VERSION=0.5.0
RUN wget -q -O /opt/hardcover.zip \
      "https://github.com/robbrazier/calibre-plugins/releases/download/hardcover-${HARDCOVER_PLUGIN_VERSION}/hardcover-${HARDCOVER_PLUGIN_VERSION}.zip"

# Bake the build's commit hash for startup logging (CI sets this to the git
# SHA). Placed last so it doesn't invalidate the cached layers above on every
# commit — only this trivial ENV layer changes.
ARG BOOKIN_COMMIT=unknown
ENV BOOKIN_COMMIT=$BOOKIN_COMMIT

# Bypass the base image's s6 init (/init) — bookin is the only process we run.
ENTRYPOINT ["/app/.venv/bin/bookin"]
