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

Docs extras (Sphinx + MyST-NB + theme):

```bash
uv sync --extra docs --dev
make docs
```

Rendered HTML lands in `docs/_build/html/`. Open `docs/_build/html/index.html` locally.

## Optional plotting

Vignettes that plot mosaics need `matplotlib` (already in the project `dev` dependency group). Install docs + dev extras together as above.
