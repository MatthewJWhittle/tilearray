# Contributing

Thanks for helping with tilearray.

tilearray is a **data-access** library: remote tiles → politely fetched, correctly labelled, lazy xarray arrays. It is not a GIS processing suite. Read [docs/design-principles.md](docs/design-principles.md) for what we will and will not do (including CRS roles and the access-versus-processing boundary). When writing user-facing docs (Sphinx, README, guides), apply [docs/docs-writing-principles.md](docs/docs-writing-principles.md) — filter for reader jobs; no milestone / stage-gate / PR archaeology. Coding agents: [AGENTS.md](AGENTS.md).

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

### What “green” means

| Gate | Command | Covers |
|------|---------|--------|
| **PR / `make test`** | `uv run pytest -m "not slow and not net"` | Unit + VCR contract + **product contracts** (`tests/unit/test_product_contracts.py`: URL taxonomy, opaque inspect journey, soft-fail phases, plot-ready dims). Coverage floor ≥ 85%. |
| **Nightly / dispatch** | `uv run pytest -m "net or slow"` (+ `scripts/docs_journey_smoke.py`) | Live smoke: EA WCS, Kartverket opaque `inspect_endpoint`, OSM plot-ready mosaic, preset soaks. |
| **Not gated** | Stage-gate notebooks, full `docs-artifacts` | Human product feel; do not treat PR green as “stage-gate would pass.” |

Product-contract tests stay **unmarked** `net`/`slow` so they are PR-required. Add a new URL-shape row or phase assert there before teaching it in docs.

Live hosts and cassette recording: [tests/contract/README.md](tests/contract/README.md).

## Pull requests

1. Branch from `main`.
2. Keep changes focused; run `make test && make lint` before opening a PR.
3. Use the **API design checklist** in [guidelines.md](guidelines.md) (§9) together with [docs/design-principles.md](docs/design-principles.md).
4. Docs-only work: follow [docs writing principles](docs/docs-writing-principles.md), then [Building the docs](docs/building-docs.md). Run `make docs`.
5. User-visible behaviour → update docs and `CHANGELOG.md` `[Unreleased]` in the same PR. Do not bump the package version unless a release is intended.
6. Before a **stable-contract / 1.0** cut (or when expanding `__all__` toward freeze): [docs/api-freeze-checklist.md](docs/api-freeze-checklist.md).

## Project conventions

Engineering standards (layout, tests, packaging, PR checklist) live in [guidelines.md](guidelines.md). Product ethos lives in [docs/design-principles.md](docs/design-principles.md). User-facing prose filter: [docs/docs-writing-principles.md](docs/docs-writing-principles.md). Public vs provisional surface and Alpha graduation: [docs/api-freeze-checklist.md](docs/api-freeze-checklist.md). Keep those roles separate so rules do not drift.
