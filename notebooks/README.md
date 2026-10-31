# Development Notebooks

Prefer the Sphinx vignettes under [`docs/vignettes/`](../docs/vignettes/) for durable, CI-built guides.

This directory remains available for local experimentation.

## Layout

- `examples/` — older exploratory notebooks (superseded by `docs/vignettes/`)
- `experiments/` — optional prototyping scratch space

## Usage

```bash
uv sync --dev
uv run jupyter lab
```

Large outputs belong in gitignored paths (`notebooks/data/`, `notebooks/outputs/`).
