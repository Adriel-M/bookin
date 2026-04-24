# bookin

Ebook folder watcher and organizer. Watches `/input` for new ebook files, fetches metadata and cover art from Amazon via Calibre CLI, and exports them to `/output` using a configurable Calibre Template Language pattern.

## Architecture

```
src/bookin/
├── cli.py        # Click entry point: --config, --once, --verbose
├── config.py     # Config dataclass; INPUT_DIR/OUTPUT_DIR constants
├── watcher.py    # watchdog daemon with file stability check + worker queue
├── processor.py  # Per-file pipeline: add → fetch → set_metadata → export → delete
├── calibre.py    # Subprocess wrappers for calibredb and fetch-ebook-metadata
└── errors.py     # Exception hierarchy
```

**Processing pipeline** (one file):
1. `calibredb add` → throwaway temp library
2. `ebook-meta` → read embedded metadata (title, authors, ISBN)
3. `fetch-ebook-metadata --allowed-plugin Amazon` → enrich + download cover (best-effort)
4. `calibredb set_metadata` → apply to library record
5. `calibredb export --template <cfg.template>` → write to `/output`
6. Delete source file from `/input`
7. `shutil.rmtree` temp library

Calibre handles all template rendering and path sanitization natively — there is no custom template engine in this project.

## Key Design Decisions

- **Configuration via environment variables** — `BOOKIN_INPUT_DIR` (default: `/input`), `BOOKIN_OUTPUT_DIR` (default: `/output`), `BOOKIN_TEMPLATE` (default: series-aware template), and `BOOKIN_LOG_LEVEL` (default: `INFO`). No config file is used.
- **Amazon is the only metadata source** (`--allowed-plugin Amazon` is hardcoded in `calibre.py:fetch_metadata`).
- **Throwaway Calibre library per file** — no persistent library is maintained. Each processed file creates and deletes its own temp library.
- **`QT_QPA_PLATFORM=offscreen`** is set in the Dockerfile. Calibre's CLI tools use Qt internally; this env var lets them run headlessly without Xvfb.
- **Metadata fetch failures are non-fatal** — if Amazon returns nothing, the file is exported using only its embedded metadata.
- **Failed files** land in `/output/_failed/` with a `.error` sidecar containing the traceback.

## Development

**Prerequisites:** `uv`, `calibre` (for local testing)

```bash
# Install calibre locally
brew install --cask calibre

# Install dependencies
uv sync

# Run daemon
uv run bookin
```

## Contributing

**Branch workflow:** When making changes, create a new branch and commit there. Do not commit directly to `main`.

```bash
git checkout -b <branch-name>
# make changes
git add <files>
git commit -m "description"
git push -u origin <branch-name>
```

Then create a PR on GitHub. CI (lint, typecheck, tests) and the Docker build must pass before merging.

## Commands

```bash
uv run pytest              # run tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy src/           # type check
```

All three must pass cleanly before committing. Tests mock all Calibre subprocess calls — no Calibre install required to run tests.

## Docker

```bash
docker compose up --build  # build and start
# drop ebooks into ./input/ — output appears in ./output/
```

The `config.yaml` is mounted read-only at `/config/config.yaml`.

## Adding Features

- **New metadata source:** modify `calibre.py:fetch_metadata` — change `--allowed-plugin` or add a fallback call
- **New output behaviour:** modify `processor.py:_process` — the pipeline is sequential and easy to extend
- **New config field:** add to `Config` dataclass in `config.py` and read the corresponding env var in `load_config()`
- **New supported file type:** add the extension to `SUPPORTED_EXTENSIONS` in `config.py`

## Known Limitations

- No retry logic for transient Calibre failures — failed files go directly to `_failed/`
- No rate limiting on the work queue — many simultaneous files will queue and process serially
- `ebook-meta` output parsing uses a line-by-line regex; may miss metadata if Calibre changes its output format
