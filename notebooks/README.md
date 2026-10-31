# Development Notebooks

Prefer the Sphinx **usage guides** under [`docs/guides/`](../docs/guides/) for durable, visual documentation.

Figures for those guides are regenerated with:

```bash
make docs-artifacts   # all docs assets (diagrams + live mosaics)
make docs             # static HTML; no tile fetches
```

This directory remains available for local experimentation.

## Layout

- `examples/` — older exploratory notebooks (superseded by `docs/guides/`)
- `experiments/` — optional prototyping scratch space

## Usage

```bash
uv sync --dev
uv run jupyter lab
```

Large outputs belong in gitignored paths (`notebooks/data/`, `notebooks/outputs/`).
