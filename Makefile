.PHONY: test lint format typecheck check fix codegen clean-codegen build up down logs

# The Hardcover client is generated from schema/ + queries/ rather than
# committed, so every target that imports bookin depends on it. Make rebuilds it
# automatically whenever the schema or a query is newer than the output.
CODEGEN_VERSION := 0.18.0
GENERATED := src/bookin/graphql_client/client.py
CODEGEN_INPUTS := schema/hardcover.graphql $(wildcard queries/*.graphql) pyproject.toml

$(GENERATED): $(CODEGEN_INPUTS)
	uv tool run --from ariadne-codegen==$(CODEGEN_VERSION) ariadne-codegen

# ── Dev ──────────────────────────────────────────────────────────────────────

test: $(GENERATED)
	uv run pytest

lint: $(GENERATED)
	uv run ruff check .

format:
	uv run ruff format .

typecheck: $(GENERATED)
	uv run mypy src/

# Run all checks (CI equivalent)
check: $(GENERATED)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src/
	uv run pytest

# Auto-fix lint issues
fix:
	uv run ruff check . --fix
	uv run ruff format .

# Regenerate the typed Hardcover client from schema/ + queries/. Run through
# `uv tool run` so ariadne-codegen's pinned ruff stays out of this project's env.
codegen: $(GENERATED)

clean-codegen:
	rm -rf src/bookin/graphql_client

# ── Docker ───────────────────────────────────────────────────────────────────

build:
	docker compose build

up:
	mkdir -p input output
	docker compose up

down:
	docker compose down

logs:
	docker compose logs -f
