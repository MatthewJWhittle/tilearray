# Install

Python **3.9+**. Prefer [uv](https://github.com/astral-sh/uv):

```bash
uv add tilearray
```

or:

```bash
pip install tilearray
```

## Development checkout

```bash
git clone https://github.com/MatthewJWhittle/tilearray.git
cd tilearray
uv sync --dev
```

## Documentation

Docs extras (Sphinx + MyST + theme + matplotlib for regenerating figures):

```bash
uv sync --extra docs --dev
make docs
```

Rendered HTML lands in `docs/_build/html/`. Open `docs/_build/html/index.html` locally.

### Regenerating usage-guide figures (network)

Visual guides embed committed PNGs under `docs/_static/guides/`. Refresh them against live public hosts with polite presets:

```bash
make docs-artifacts
# or: uv run --extra docs python scripts/generate_docs_artifacts.py
```

Then rebuild HTML with `make docs`. The Sphinx build itself does **not** fetch tiles.
