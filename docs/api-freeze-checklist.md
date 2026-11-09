# Public API freeze checklist

Maintainer / agent gate for the **stable contract** cut (1.0). Use this before removing the Alpha badge or promising SemVer stability — not as permission to cut 1.0 early.

**Audience:** people changing `__all__`, defaults, or release metadata.  
**Not for:** day-to-day bug fixes (use {doc}`design-principles` + [guidelines.md](https://github.com/MatthewJWhittle/tilearray/blob/main/guidelines.md) §9).  
**Tracking issue:** [#62](https://github.com/MatthewJWhittle/tilearray/issues/62).

**Status today:** package is **0.3.x Alpha**. This page is the checklist and inventory; boxes under [Graduation gate](#graduation-gate-10) stay open until exit criteria hold. **Do not** treat this checklist as a 1.0 cut or as dropping Alpha.

---

## What “frozen” means here

| Kind | Meaning |
|------|---------|
| **Public** | In package `__all__` and/or Sphinx {doc}`api/index` — callers may depend on it |
| **Provisional** | Public today, but may shrink, rename, or get a deprecation before 1.0 |
| **Internal** | Importable under submodules; **no** stability promise — may change without deprecation |
| **Shipped (Alpha)** | On `main` in 0.3.x; part of today’s contract, still under Alpha (may change before 1.0) |

SemVer for 1.0 applies to the **public** surface below (plus any symbols promoted from provisional with an explicit decision). Internal imports are unsupported.

---

## Inventory on `main` (0.3.x Alpha)

Source of truth: `tilearray.__all__` and {doc}`api/index`. Refresh this table when exports change.

### Core path (freeze intent)

These are the SemVer spine for 1.0:

| Symbol | Role |
|--------|------|
| `create_array` | Primary entry — plan / bind / lazy mosaic |
| `load_array` | Eager sugar (`create_array(..., compute=True)`); not a second habit |
| `compute_mosaic` | Canonical compute helper (Dask workers ↔ fetch ceiling); process-local gate documented |
| `preview_array` / `ArrayPreview` | Preflight — request count, grid, concurrency, cache warm/cold, unknown band/byte counts where not knowable (#61 / #109) |
| `recommend_grid_shape` | Pure AOI helper for resolution × chunk sizing (#61 / #109) |
| `fetch_summary` / `FetchSummary` | Post-plan / post-compute progress & pressure counters (#61 / #109) |
| `inspect_endpoint` / `inspect_capabilities` / `EndpointInspection` (+ `ResourceSummary`, `MatrixSetSummary`) | Discover → choose → validated config from GetCapabilities (#96 / #108) |
| `WCSConfig` / `WMSConfig` / `WMTSConfig` / `XYZConfig` | Protocol configs (`from_url`, fetch knobs) |
| `ServiceConfig` | Shared config base |
| `FetchPolicy` | Shared fetch / AIMD / breaker policy (process-local) |
| `TileArrayError` and subclasses (`NetworkError`, `ValidationError`, `ParseError`, `ConfigurationError`, `ServiceError`) | Public error taxonomy |
| `TileErrorAction` / `TileErrorContext` / `TileErrorHandler` / `soft_fail_exhausted_tiles` | Opt-in soft-fail surface (plan-time bind) |

Also part of the taught contract (module import, not always re-exported on the package root):

| Surface | Role |
|---------|------|
| `tilearray.fetch_presets` (`ea_dsp_fetch_defaults`, `osm_fetch_defaults`, host URL/coverage constants) | Host politeness — compose with `from_url`, no new `for_*` |
| Mosaic attrs documented in how-it-works / API (`crs`, `transform`, `transform_source`, `band_policy`, `palette_mode`, `dtype_policy`, soft-fail / fetch-policy attrs, …) | Analysis handoff; treat documented keys as public behaviour |

### Types & registry (provisional toward 1.0)

Exported today; decide keep / demote / document before freeze:

| Symbol | Note |
|--------|------|
| `CRS`, `Format`, `BoundingBox`, `BBoxTuple` | Caller-facing; `CRS` accepts any pyproj-valid projected/geographic CRS (#92 / #105). Prefer public `bbox_crs=` for the query CRS; `bbox_crs=` is required during alpha. WCS/WMS label mosaics with the query CRS; XYZ/slippy label `EPSG:3857` tile-union; metadata-backed WMTS labels with matrix SupportedCRS (`attrs["bbox_crs"]` records the query; `attrs["mosaic_crs"]` is the pixel-grid CRS) — see {doc}`design-principles` |
| `TileRequest`, `TileResponse`, `TileGeometry` | Useful for advanced callers; candidates to keep thin or document as “supported advanced” |
| `CoverageDescription`, `ServiceCapabilities`, `SpatialExtent`, `TemporalExtent`, `WCSResponse`, `ServiceTypeEnum` | Capability / parse shapes — prefer not growing these as a second API |
| `detect_service_type`, `get_service`, `register_service` | Registry hooks; keep for extension, avoid teaching as the happy path |

### Explicitly internal (not in `__all__`)

Import at your own risk (paths may move):

- Concrete services and parsers (`WCSService`, `WMSService`, `WMTSService`, `XYZService`, `BaseService`, `WCSParser`, `WMTSParser`, `WMSParser`, …)
- `TileFetcher`, pressure / AIMD internals, cassette helpers, private `_` modules
- Anything under tests / scripts / notebooks

---

## Shipped uniqueness / DX surface (refresh 2026-11-04)

These landed on `main` during the post-0.2.0 uniqueness / DX queue. They are **Alpha-shipped**, not a 1.0 freeze. Refresh again before any freeze cut.

| PR | Issue | What shipped |
|----|-------|--------------|
| [#105](https://github.com/MatthewJWhittle/tilearray/pull/105) | [#92](https://github.com/MatthewJWhittle/tilearray/issues/92) | Wider `CRS` acceptance (pyproj / any valid EPSG); same `create_array(..., bbox_crs=)` path |
| [#106](https://github.com/MatthewJWhittle/tilearray/pull/106) | [#94](https://github.com/MatthewJWhittle/tilearray/issues/94) / [#95](https://github.com/MatthewJWhittle/tilearray/issues/95) | Clipped WCS GeoTIFF placement; default `band_policy="preserve"` / palette / dtype contract |
| [#107](https://github.com/MatthewJWhittle/tilearray/pull/107) | [#93](https://github.com/MatthewJWhittle/tilearray/issues/93) | Native TileMatrixSet planning / mosaic CRS labelling |
| [#110](https://github.com/MatthewJWhittle/tilearray/pull/110) | [#60](https://github.com/MatthewJWhittle/tilearray/issues/60) | WMTS host guide + Kartverket contract tests; auto-load caps for non-slippy matrices |
| [#108](https://github.com/MatthewJWhittle/tilearray/pull/108) | [#96](https://github.com/MatthewJWhittle/tilearray/issues/96) | `inspect_endpoint` / `inspect_capabilities` / `EndpointInspection.build_config` |
| [#109](https://github.com/MatthewJWhittle/tilearray/pull/109) | [#61](https://github.com/MatthewJWhittle/tilearray/issues/61) | `recommend_grid_shape`, `preview_array` / `ArrayPreview`, `fetch_summary` / `FetchSummary` |
| [#112](https://github.com/MatthewJWhittle/tilearray/pull/112) | [#87](https://github.com/MatthewJWhittle/tilearray/issues/87) | Multi-AOI / ML sample-loop **docs + offline tests** — **no** orchestration API |
| [#113](https://github.com/MatthewJWhittle/tilearray/pull/113) | [#59](https://github.com/MatthewJWhittle/tilearray/issues/59) | Process-local fetch-gate docs; warn on non-`threads` scheduler — **no** distributed coordinator |
| [#116](https://github.com/MatthewJWhittle/tilearray/pull/116) / [#103](https://github.com/MatthewJWhittle/tilearray/pull/103) | [#97](https://github.com/MatthewJWhittle/tilearray/issues/97) | Worked examples notebook + guide — **no** new library exports |
| [#104](https://github.com/MatthewJWhittle/tilearray/pull/104) | docs filter | Maintainer docs-writing principles / leakage seatbelt — **no** runtime API |

**Still not a public orchestration API:** multi-AOI remains a documented loop ([multi-aoi-ml-patches.md](multi-aoi-ml-patches.md)); fetch gates remain process-local. Design before inventing `map_aois` or a cross-process coordinator.

---

## Draft SemVer promise (publish at 1.0)

Until 1.0, Alpha may change; still prefer additive changes and documented CHANGELOG behaviour changes ({doc}`design-principles`).

At **1.0.0**, publish something equivalent to:

1. **Compatible** (minor/patch): additive exports; new optional kwargs with safe defaults; new error subclasses under `TileArrayError`; docs/clarifications; bug fixes that restore documented behaviour.
2. **Breaking** (major): remove/rename anything in `__all__` / API reference; change default fail / abort / soft-fill / band / CRS / politeness semantics; change meaning of `create_array` / config fields / `compute_mosaic` / `preview_array` / `inspect_endpoint` without a documented migration.
3. **Spine that must not break silently:** `create_array`, protocol `*Config` + `from_url` compose path, `FetchPolicy` host-ceiling rules, error hierarchy at public boundaries, `compute_mosaic`, `preview_array` / `fetch_summary`, `inspect_endpoint` / `build_config`, documented soft-fail presets (`soft_fail_exhausted_tiles` / `on_tile_error` plan-time bind).
4. **Out of promise:** submodule internals, undocumented attrs, registry plugins beyond documented hooks, live-host availability.

---

## Avoiding breaking changes (every PR)

Use with [guidelines.md](https://github.com/MatthewJWhittle/tilearray/blob/main/guidelines.md) §9 and {doc}`design-principles`:

- [ ] Prefer **additive** APIs; new required kwargs need a strong reason and a migration note.
- [ ] Do not change default hard-fail / abort / NaN / CRS / band / host-ceiling behaviour without CHANGELOG **Breaking** or **Changed** + docs in the same PR.
- [ ] New public names → `__all__` + {doc}`api/index` autosummary together; no accidental re-exports of parsers/services.
- [ ] No second compute or load habit; no new `for_*` host methods (compose `fetch_presets` + `from_url`).
- [ ] Plan-time bind for load behaviour; no post-plan monkeypatch APIs.
- [ ] Deprecate before remove once past Alpha (warn → docs → remove on next major).
- [ ] If a PR lands a default or export change, update **this** inventory in the same theme before calling the surface frozen.

---

## Graduation gate (1.0)

Do **not** cut 1.0 until every box is true (or an unchecked item is explicitly accepted in docs as a known limit). Maps to [#62](https://github.com/MatthewJWhittle/tilearray/issues/62). **Leaving these unchecked is intentional** — Alpha stays until uniqueness/scale work settles.

### Public vs private

- [ ] `__all__` + API reference match; provisional symbols either promoted, demoted with docs, or deprecated
- [ ] Inspect / preview / fetch-summary surfaces inventoried above either confirmed for 1.0 or explicitly provisional
- [ ] Deprecation cycle complete for anything removed between last Alpha and 1.0

### SemVer + messaging

- [ ] SemVer promise published (README / docs — this section or a short “Stability” note)
- [ ] Alpha badge / classifier removed (`README`, docs admonition, `pyproject` Development Status)
- [ ] CHANGELOG has a clear 1.0 theme entry (stable contract, not a grab-bag)

### Product exit criteria

- [ ] North-star success criteria on {doc}`roadmap` hold on the **supported Python matrix** (today CI: 3.9–3.12; extend only if tested)
- [ ] No open **S0** gaps; **S1** gaps closed or documented as known limits
- [ ] Remaining uniqueness / scale themes that would redefine CRS roles, decode defaults, or host coordination have landed or been explicitly deferred

### Release hygiene

- [ ] Provenance / attestations on the 1.0 artifact (Trusted Publishing / PEP 740 path already wired — verify on the tag)
- [ ] Release checklist boring: offline `make test` + `make lint` + `make docs`; tag; PyPI; Pages; no surprise version bump in the freeze PR itself

---

## Related

- [design-principles.md](design-principles.md) — ethos, CRS roles, scope
- [roadmap.md](roadmap.md) — coverage today, direction, non-goals
- [api/index.md](api/index.md) — advertised reference
- [building-docs.md](building-docs.md) — Sphinx / figures
- [AGENTS.md](https://github.com/MatthewJWhittle/tilearray/blob/main/AGENTS.md) · [guidelines.md](https://github.com/MatthewJWhittle/tilearray/blob/main/guidelines.md) · [CONTRIBUTING.md](https://github.com/MatthewJWhittle/tilearray/blob/main/CONTRIBUTING.md)
