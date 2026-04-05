.PHONY: test lint format typecheck check fix build up down logs run

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
	uv run mypy src/
	uv run pytest

# Auto-fix lint issues
fix:
	uv run ruff check . --fix
	uv run ruff format .

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

# One-shot: process any files currently in ./input and exit (no daemon)
run:
	mkdir -p input output
	docker compose run --rm bookin --config /config/config.yaml --once
