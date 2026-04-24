# bookin

> Built with [Claude Code](https://claude.ai) + [opencode](https://opencode.ai)

Drop ebooks into a folder — bookin automatically fetches metadata and cover art from Amazon via [Calibre](https://calibre-ebook.com), then organizes them into a structured output folder using a pattern you define.

## How it works

1. Drop an ebook (`.epub`, `.mobi`, `.azw3`, `.pdf`, etc.) into the input folder
2. bookin detects the new file, reads its embedded metadata, and queries Amazon for enriched metadata + cover
3. The book is exported to the output folder using your configured template (e.g. `{authors}/{title}`)
4. The source file is removed from the input folder

Files that fail are moved to `output/_failed/` with an `.error` sidecar describing what went wrong.

## Quick start (Docker)

```bash
# 1. Create local folders
mkdir -p input output

# 2. Start
docker compose up -d

# 3. Drop an ebook in
cp my-book.epub input/
```

Output appears in `./output/` organized by your template.

## Configuration

Configuration is done via environment variables:

| Variable | Default | Description |
|---|---|---|
| `BOOKIN_TEMPLATE` | `{authors}/{title}` | Output path template |
| `BOOKIN_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |

Set them in `docker-compose.yml`:

```yaml
environment:
  - BOOKIN_TEMPLATE={authors}/{title}
  - BOOKIN_LOG_LEVEL=INFO
```

The `BOOKIN_TEMPLATE` field uses [Calibre Template Language](https://manual.calibre-ebook.com/template_lang.html) — the same syntax as Calibre's own "Save to disk" feature.

**Common fields:**

| Field | Example |
|---|---|
| `{authors}` | `Frank Herbert` |
| `{title}` | `Dune` |
| `{series}` | `Dune Chronicles` |
| `{series_index}` | `1` |
| `{publisher}` | `Ace Books` |
| `{pubdate}` | `1965` |

**Template examples:**

```
# Author / Title
{authors}/{title}

# Author / Series N - Title
{authors}/{series}/{series_index} - {title}

# Flat: Author - Title
{authors} - {title}
```

## Supported formats

`.epub` `.mobi` `.azw` `.azw3` `.pdf` `.djvu` `.fb2`

## Local development

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/getting-started/installation/), [Calibre](https://calibre-ebook.com/download)

```bash
# Install Calibre (macOS)
brew install --cask calibre

# Install Python dependencies
uv sync

# Run daemon (watch /input continuously)
uv run bookin

# Debug logging
uv run bookin --verbose
```

## Commands

**Development:**
```bash
make test       # run tests
make lint       # lint
make format     # format
make typecheck  # type check
make check      # all of the above (lint + types + tests)
make fix        # auto-fix lint and formatting
```

**Docker:**
```bash
make build      # build image
make up         # start daemon (watches ./input/)
make down       # stop
make logs       # follow logs
make run        # one-shot: process ./input/ and exit
```

## Docker volume mounts

| Mount | Purpose |
|---|---|
| `./input:/input` | Drop ebooks here |
| `./output:/output` | Organized output |

Input and output paths are fixed to `/input` and `/output` inside the container.

## License

MIT
