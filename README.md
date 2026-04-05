# bookin

Drop ebooks into a folder — bookin automatically fetches metadata and cover art from Amazon via [Calibre](https://calibre-ebook.com), then organizes them into a structured output folder using a pattern you define.

## How it works

1. Drop an ebook (`.epub`, `.mobi`, `.azw3`, `.pdf`, etc.) into the input folder
2. bookin detects the new file, reads its embedded metadata, and queries Amazon for enriched metadata + cover
3. The book is exported to the output folder using your configured template (e.g. `{authors}/{title}/{title}`)
4. The source file is removed from the input folder

Files that fail are moved to `output/_failed/` with an `.error` sidecar describing what went wrong.

## Quick start (Docker)

```bash
# 1. Copy and edit config
cp config.yaml.example config.yaml

# 2. Create local folders
mkdir -p input output

# 3. Start
docker compose up -d

# 4. Drop an ebook in
cp my-book.epub input/
```

Output appears in `./output/` organized by your template.

## Configuration

```yaml
# config.yaml
template: "{authors}/{title}/{title}"
log_level: INFO
```

The `template` field uses [Calibre Template Language](https://manual.calibre-ebook.com/template_lang.html) — the same syntax as Calibre's own "Save to disk" feature.

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

```yaml
# Author / Title / Title.epub
template: "{authors}/{title}/{title}"

# Author / Series N - Title / Title.epub
template: "{authors}/{series}/{series_index} - {title}/{title}"

# Flat: Author - Title.epub
template: "{authors} - {title}"
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

# Run one-shot (process existing files in /input and exit)
uv run bookin --config config.yaml --once

# Run daemon (watch /input continuously)
uv run bookin --config config.yaml

# Debug logging
uv run bookin --config config.yaml --verbose
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
| `./config.yaml:/config/config.yaml:ro` | Configuration |

Input and output paths are fixed to `/input` and `/output` inside the container.

## License

MIT
