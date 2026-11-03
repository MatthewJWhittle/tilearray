# Contributing

Thanks for helping with tilearray.

tilearray is a **data-access** library: remote tiles → politely fetched, correctly labelled, lazy xarray arrays. It is not a GIS processing suite. Read [docs/design-principles.md](docs/design-principles.md) for what we will and will not do (including CRS roles and the access-versus-processing boundary). Coding agents: [AGENTS.md](AGENTS.md).

## Setup

```bash
git clone https://github.com/MatthewJWhittle/tilearray.git
cd tilearray
uv sync --dev
make pre-commit
```

## Checks

```bash
make test
make lint
make docs          # HTML under docs/_build/html/
make docs-artifacts  # regenerate committed figures (network for live guides)
```

## Pull requests

1. Branch from `main`.
2. Keep changes focused; run `make test && make lint` before opening a PR.
3. Use the **API design checklist** in [guidelines.md](guidelines.md) (§9) together with [docs/design-principles.md](docs/design-principles.md).
4. Docs-only work: see [Building the docs](docs/building-docs.md).
5. User-visible behaviour → update docs and `CHANGELOG.md` `[Unreleased]` in the same PR. Do not bump the package version unless a release is intended.

## Project conventions

Engineering standards (layout, tests, packaging, PR checklist) live in [guidelines.md](guidelines.md). Product ethos lives in [docs/design-principles.md](docs/design-principles.md) — keep those roles separate so rules do not drift.
