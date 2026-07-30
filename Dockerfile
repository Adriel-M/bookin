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

# The Hardcover GraphQL client is generated from the vendored schema plus our
# operations rather than committed, so it has to be built before the project is
# installed. Pinned so the image is reproducible.
ARG ARIADNE_CODEGEN_VERSION=0.18.0
# Only the schema itself, not the whole hardcover-docs submodule (~18 MB of
# documentation assets we have no use for).
COPY vendor/hardcover-docs/schema.graphql ./vendor/hardcover-docs/schema.graphql
COPY queries/ ./queries/
RUN uv tool run --from "ariadne-codegen==${ARIADNE_CODEGEN_VERSION}" ariadne-codegen \
    && test -f src/bookin/graphql_client/client.py

RUN uv sync --frozen --no-dev

# Bake the build's commit hash for startup logging (CI sets this to the git
# SHA). Placed last so it doesn't invalidate the cached layers above on every
# commit — only this trivial ENV layer changes.
ARG BOOKIN_COMMIT=unknown
ENV BOOKIN_COMMIT=$BOOKIN_COMMIT

# Bypass the base image's s6 init (/init) — bookin is the only process we run.
ENTRYPOINT ["/app/.venv/bin/bookin"]
