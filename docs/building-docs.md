# Building the docs

Maintainer notes for regenerating Sphinx assets. End users can ignore this page.

## HTML site (offline)

```bash
uv sync --extra docs --dev
make docs
# open docs/_build/html/index.html
```

CI runs the same Sphinx build; it does not fetch tiles. On pushes to `main`, the Docs workflow also deploys HTML to **GitHub Pages**:

https://matthewjwhittle.github.io/tilearray/

## Regenerating figures

Committed PNGs live under `docs/_static/guides/`.

```bash
make docs-artifacts
```

That rebuilds How it works diagrams (offline) and live usage-guide mosaics (network, polite presets). Commit updated assets when they change.

## Politeness soak (opt-in)

PR CI stays offline (`not slow and not net`). Live preset soaks are opt-in:

```bash
# Multi-tile soak under EA + OSM ceilings (also tagged @net / @slow)
uv run pytest -m soak --timeout=300 --cov-fail-under=0

# Larger AOIs on a laptop or bigger machine
uv run python scripts/soak_large_aoi_presets.py --host both --profile medium
uv run python scripts/soak_large_aoi_presets.py --host ea --profile large
```

Do not wire large profiles into always-on PR jobs. User-facing fetch behaviour: {doc}`how-it-works`.
