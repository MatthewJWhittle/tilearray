# Building the docs

Maintainer notes for regenerating Sphinx assets. End users can ignore this page.

## HTML site (offline)

```bash
uv sync --extra docs --dev
make docs
# open docs/_build/html/index.html
```

CI runs the same Sphinx build; it does not fetch tiles.

## Regenerating figures

Committed PNGs live under `docs/_static/guides/`.

```bash
make docs-artifacts
```

That rebuilds How it works diagrams (offline) and live usage-guide mosaics (network, polite presets). Commit updated assets when they change.
