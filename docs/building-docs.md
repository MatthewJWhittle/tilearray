# Building the docs

Maintainer notes for regenerating Sphinx assets and cutting a release. End users can ignore this page.

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

## First open release checklist (`0.1.0`)

Do **not** push the release tag until the items below that need human secrets/clicks are done.

### Before tagging

1. Version is `0.1.0` in `pyproject.toml` and `src/tilearray/_version.py`.
2. CHANGELOG `[0.1.0]` has a real date (replace `TBD`) and no leftover release notes stuck under `[Unreleased]`.
3. Docs CI green; Pages source set to **GitHub Actions** (repo Settings → Pages).
4. Confirm hosted docs URL loads after a `main` deploy.
5. `Project.urls.Documentation` and the README Docs link match the Pages URL.
6. PyPI: create project/token (or Trusted Publisher). Store classic token as repo secret `PYPI_API_TOKEN` **or** configure Trusted Publishing for `.github/workflows/build.yml` / environment `pypi`.

### Tag and publish

```bash
git tag -a v0.1.0 -m "tilearray 0.1.0"
git push origin v0.1.0
```

Or create a GitHub Release for `v0.1.0` (same tag). That runs **Build and Publish**, which uploads to PyPI via `uv publish`.

### After publish

1. `pip install tilearray==0.1.0` in a clean venv and run a quickstart snippet.
2. Check https://pypi.org/project/tilearray/ metadata and Documentation link.
3. Spot-check the Pages site (install page, one usage guide, API index).
