.PHONY: test lint format typecheck check fix codegen build up down logs

# ── Dev ──────────────────────────────────────────────────────────────────────

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy src/

# Run all checks (CI equivalent)
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src/
	uv run pytest

# Auto-fix lint issues
fix:
	uv run ruff check . --fix
	uv run ruff format .

# Regenerate the typed Hardcover client from schema/ + queries/.
# Run via uvx so ariadne-codegen's pinned ruff stays out of this project's env.
codegen:
	uvx --from ariadne-codegen==0.18.0 ariadne-codegen

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
