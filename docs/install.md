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
make docs-how-it-works-figures   # offline stage diagrams for How it works
make docs-artifacts              # live hosts → usage-guide mosaics (network)
make docs                        # offline HTML build
```

The Sphinx build itself does **not** fetch tiles.
