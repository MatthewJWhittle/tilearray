# Agent guidance — tilearray

Read these before changing code or docs:

1. **[docs/design-principles.md](docs/design-principles.md)** — canonical product/API ethos, CRS roles, scope/non-goals (do not fork a second copy).
2. **[guidelines.md](guidelines.md)** — engineering standards, tests, API design checklist.
3. **[docs/roadmap.md](docs/roadmap.md)** — milestones and non-goals; do not invent scope.
4. **[CONTRIBUTING.md](CONTRIBUTING.md)** — setup and PR habits.

## Before you edit

- [ ] Does the change fit **data access** (plan / fetch / decode / assemble), not GIS processing?
- [ ] Are CRS roles clear — query vs service/request vs **output** — with no silent local warp?
- [ ] Are mosaic metadata (CRS, transform, extent, bands/nodata) accurate for what was returned?
- [ ] Defaults stay hard-fail + polite host ceilings; complexity is plan-time opt-in?
- [ ] Public names match existing vocabulary; no ambiguous overload of `crs` / dual compute habits?
- [ ] GDAL-free default path preserved; host quirks compose via `fetch_presets` + `from_url` (no new `for_*`)?
- [ ] Tests cover the dangerous case offline; docs/CHANGELOG updated for user-visible behaviour?

## Commands

```bash
uv sync --dev
make test          # offline PR suite (slow/net deselected by default)
make lint
make docs          # Sphinx HTML; offline
```

Live guide figures: `make docs-artifacts` (network). Do not bump the package version unless asked. One theme per PR.
