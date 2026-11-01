# Contributing

Thanks for helping with tilearray.

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
3. Docs-only work: see [Building the docs](docs/building-docs.md).

## Project conventions

Contributor and agent policy (layout, tests, packaging) lives in [guidelines.md](guidelines.md). That file is for people changing the repo — not product documentation for library users.
