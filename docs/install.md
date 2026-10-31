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

### Regenerating figures

Committed PNGs live under `docs/_static/guides/`.

```bash
make docs-artifacts   # all assets: How it works diagrams + live usage-guide figures
make docs             # offline Sphinx HTML (no tile fetches)
```

`docs-artifacts` needs network for the live mosaics; stage diagrams are generated offline in the same target. The Sphinx build itself does **not** fetch tiles.
