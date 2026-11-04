# Agent guidance — tilearray

Read these before changing code or docs:

1. **[docs/design-principles.md](docs/design-principles.md)** — canonical product/API ethos, CRS roles, scope/non-goals (do not fork a second copy).
2. **[docs/docs-writing-principles.md](docs/docs-writing-principles.md)** — user-facing docs filter (Diátaxis jobs; cut milestone / stage-gate / PR archaeology).
3. **[guidelines.md](guidelines.md)** — engineering standards, tests, API design checklist.
4. **[docs/roadmap.md](docs/roadmap.md)** — coverage, direction, non-goals; do not invent scope.
5. **[CONTRIBUTING.md](CONTRIBUTING.md)** — setup and PR habits.

## Before you edit

- [ ] Does the change fit **data access** (plan / fetch / decode / assemble), not GIS processing?
- [ ] Are CRS roles clear — query vs service/request vs **output** — with no silent local warp?
- [ ] Are mosaic metadata (CRS, transform, extent, bands/nodata) accurate for what was returned?
- [ ] Defaults stay hard-fail + polite host ceilings; complexity is plan-time opt-in?
- [ ] Public names match existing vocabulary; no ambiguous overload of `crs` / dual compute habits?
- [ ] GDAL-free default path preserved; host quirks compose via `fetch_presets` + `from_url` (no new `for_*`)?
- [ ] Tests cover the dangerous case offline; docs/CHANGELOG updated for user-visible behaviour?

## Docs PRs (must pass)

When editing `README.md`, Sphinx pages, or usage guides, apply [docs/docs-writing-principles.md](docs/docs-writing-principles.md):

- [ ] Every changed section answers a **reader job** (install / fetch / understand / look up / fix).
- [ ] No milestone chatter, soft-exit / exit-criteria checklists, stage-gate process, or agent/PR archaeology in user-facing pages.
- [ ] Current vs target behaviour is labelled; do not document unshipped API as fact.
- [ ] Prefer delete/condense; keep visual guides / figures pipeline; no version bump unless asked.

Run `make docs` before opening the PR.

## Commands

```bash
uv sync --dev
make test          # offline PR suite (slow/net deselected by default)
make lint
make docs          # Sphinx HTML; offline
```

Live guide figures: `make docs-artifacts` (network). Do not bump the package version unless asked. One theme per PR.
