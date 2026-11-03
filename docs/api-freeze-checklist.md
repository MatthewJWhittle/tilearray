# Public API freeze checklist

Maintainer / agent gate for the **stable contract** cut (1.0). Use this before removing the Alpha badge or promising SemVer stability — not as permission to cut 1.0 early.

**Audience:** people changing `__all__`, defaults, or release metadata.  
**Not for:** day-to-day bug fixes (use {doc}`design-principles` + [guidelines.md](https://github.com/MatthewJWhittle/tilearray/blob/main/guidelines.md) §9).  
**Tracking issue:** [#62](https://github.com/MatthewJWhittle/tilearray/issues/62).

**Status today:** package is **0.2.x Alpha**. This page is the checklist and inventory; boxes under [Graduation gate](#graduation-gate-10) stay open until exit criteria hold.

---

## What “frozen” means here

| Kind | Meaning |
|------|---------|
| **Public** | In package `__all__` and/or Sphinx {doc}`api/index` — callers may depend on it |
| **Provisional** | Public today, but may shrink, rename, or get a deprecation before 1.0 |
| **Internal** | Importable under submodules; **no** stability promise — may change without deprecation |
| **Pending** | Proposed on an open PR; **not** on `main` until merged — do not document as shipped |

SemVer for 1.0 applies to the **public** surface below (plus any symbols promoted from provisional with an explicit decision). Internal imports are unsupported.

---

## Inventory on `main` (0.2.0)

Source of truth: `tilearray.__all__` and {doc}`api/index`. Refresh this table when exports change.

### Core path (freeze intent)

These are the SemVer spine for 1.0:

| Symbol | Role |
|--------|------|
| `create_array` | Primary entry — plan / bind / lazy mosaic |
| `load_array` | Eager sugar (`create_array(..., compute=True)`); not a second habit |
| `compute_with_policy` | Canonical compute helper (Dask workers ↔ fetch ceiling) |
| `WCSConfig` / `WMSConfig` / `WMTSConfig` / `XYZConfig` | Protocol configs (`from_url`, fetch knobs) |
| `ServiceConfig` | Shared config base |
| `FetchPolicy` | Shared fetch / AIMD / breaker policy |
| `TileArrayError` and subclasses (`NetworkError`, `ValidationError`, `ParseError`, `ConfigurationError`, `ServiceError`) | Public error taxonomy |
| `TileErrorAction` / `TileErrorContext` / `TileErrorHandler` / `soft_fail_exhausted_tiles` | Opt-in soft-fail surface (plan-time bind) |

Also part of the taught contract (module import, not always re-exported on the package root):

| Surface | Role |
|---------|------|
| `tilearray.fetch_presets` (`ea_dsp_fetch_defaults`, `osm_fetch_defaults`, host URL/coverage constants) | Host politeness — compose with `from_url`, no new `for_*` |
| Mosaic attrs documented in how-it-works / API (`crs`, `transform`, `transform_source`, `band_policy`, soft-fail / fetch-policy attrs, …) | Analysis handoff; treat documented keys as public behaviour |

### Types & registry (provisional toward 1.0)

Exported today; decide keep / demote / document before freeze:

| Symbol | Note |
|--------|------|
| `CRS`, `Format`, `BoundingBox`, `BBoxTuple` | Caller-facing; `CRS` still a **single** bbox+label role on `create_array` (target: separate query vs output — see {doc}`design-principles`) |
| `TileRequest`, `TileResponse`, `TileGeometry` | Useful for advanced callers; candidates to keep thin or document as “supported advanced” |
| `CoverageDescription`, `ServiceCapabilities`, `SpatialExtent`, `TemporalExtent`, `WCSResponse`, `ServiceTypeEnum` | Capability / parse shapes — prefer not growing these as a second API |
| `detect_service_type`, `get_service`, `register_service` | Registry hooks; keep for extension, avoid teaching as the happy path |

### Explicitly internal (not in `__all__`)

Import at your own risk (paths may move):

- Concrete services and parsers (`WCSService`, `WMSService`, `WMTSService`, `XYZService`, `BaseService`, `WCSParser`, `WMTSParser`, …)
- `TileFetcher`, pressure / AIMD internals, cassette helpers, private `_` modules
- Anything under tests / scripts / notebooks

---

## Pending surface (open PRs — not merged)

Snapshot **2026-11-03**. Many uniqueness / DX PRs are still open. **Do not** treat their APIs as frozen, and **do not** cut 1.0 while this queue is unresolved without an explicit “accept as known limit” decision.

Refresh by listing open PRs against `main` before any freeze cut.

| PR | Issue | Likely public impact if merged |
|----|-------|--------------------------------|
| [#103](https://github.com/MatthewJWhittle/tilearray/pull/103) | [#97](https://github.com/MatthewJWhittle/tilearray/issues/97) | Docs / notebook worked examples — **no** new library exports |
| [#104](https://github.com/MatthewJWhittle/tilearray/pull/104) | docs filter | Maintainer docs + leakage seatbelt — **no** runtime API |
| [#105](https://github.com/MatthewJWhittle/tilearray/pull/105) | [#92](https://github.com/MatthewJWhittle/tilearray/issues/92) | Wider `CRS` acceptance (pyproj / any valid EPSG); same `create_array(..., crs=)` path — **behaviour widen**, watch ValidationError contract |
| [#106](https://github.com/MatthewJWhittle/tilearray/pull/106) | [#94](https://github.com/MatthewJWhittle/tilearray/issues/94) / [#95](https://github.com/MatthewJWhittle/tilearray/issues/95) | Clipped WCS placement; **default** `band_policy` / palette / dtype honesty — treat default decode changes as **breaking** for Alpha consumers |
| [#107](https://github.com/MatthewJWhittle/tilearray/pull/107) | [#93](https://github.com/MatthewJWhittle/tilearray/issues/93) | Native TileMatrixSet planning / mosaic CRS labelling — behaviour + attrs; depends on #105 |
| [#108](https://github.com/MatthewJWhittle/tilearray/pull/108) | [#96](https://github.com/MatthewJWhittle/tilearray/issues/96) | **New** discover surface: `inspect_endpoint` / `inspect_capabilities` / `build_config` (+ types) — design-review before freeze |
| [#109](https://github.com/MatthewJWhittle/tilearray/pull/109) | [#61](https://github.com/MatthewJWhittle/tilearray/issues/61) | **New** preflight surface: `recommend_grid_shape`, `plan_array` / `ArrayPlan`, `fetch_summary` / `FetchSummary` |
| [#110](https://github.com/MatthewJWhittle/tilearray/pull/110) | [#60](https://github.com/MatthewJWhittle/tilearray/issues/60) | WMTS guide + contract tests; small planning gaps — mostly docs; stacked on #107 |

**Still open without a freeze-ready story (later roadmap):** multi-AOI / Dask-list enablement ([#87](https://github.com/MatthewJWhittle/tilearray/issues/87)), distributed “don’t stampede” ([#59](https://github.com/MatthewJWhittle/tilearray/issues/59)). Design before any public orchestration API.

---

## Draft SemVer promise (publish at 1.0)

Until 1.0, Alpha may change; still prefer additive changes and CHANGELOG honesty ({doc}`design-principles`).

At **1.0.0**, publish something equivalent to:

1. **Compatible** (minor/patch): additive exports; new optional kwargs with safe defaults; new error subclasses under `TileArrayError`; docs/clarifications; bug fixes that restore documented behaviour.
2. **Breaking** (major): remove/rename anything in `__all__` / API reference; change default fail / abort / soft-fill / band / CRS / politeness semantics; change meaning of `create_array` / config fields / `compute_with_policy` without a documented migration.
3. **Spine that must not break silently:** `create_array`, protocol `*Config` + `from_url` compose path, `FetchPolicy` host-ceiling rules, error hierarchy at public boundaries, `compute_with_policy`, documented soft-fail presets (`soft_fail_exhausted_tiles` / `on_tile_error` plan-time bind).
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
- [ ] If a pending PR (#105–#110 class) lands a default or export change, update **this** inventory in the same theme before calling the surface frozen.

---

## Graduation gate (1.0)

Do **not** cut 1.0 until every box is true (or an unchecked item is explicitly accepted in docs as a known limit). Maps to [#62](https://github.com/MatthewJWhittle/tilearray/issues/62).

### Public vs private

- [ ] `__all__` + API reference match; provisional symbols either promoted, demoted with docs, or deprecated
- [ ] Pending PR surfaces (#108 / #109 class) either merged and inventoried, or deferred with no docs claiming they ship
- [ ] Deprecation cycle complete for anything removed between last Alpha and 1.0

### SemVer + messaging

- [ ] SemVer promise published (README / docs — this section or a short “Stability” note)
- [ ] Alpha badge / classifier removed (`README`, docs admonition, `pyproject` Development Status)
- [ ] CHANGELOG has a clear 1.0 theme entry (stable contract, not a grab-bag)

### Product exit criteria

- [ ] North-star success criteria on {doc}`roadmap` hold on the **supported Python matrix** (today CI: 3.9–3.12; extend only if tested)
- [ ] No open **S0** gaps; **S1** gaps closed or documented as known limits
- [ ] Uniqueness / scale themes that redefine CRS, decode defaults, or WMTS georef have landed or been explicitly deferred (do not freeze mid-#105–#110 churn)

### Release hygiene

- [ ] Provenance / attestations on the 1.0 artifact (Trusted Publishing / PEP 740 path already wired — verify on the tag)
- [ ] Release checklist boring: offline `make test` + `make lint` + `make docs`; tag; PyPI; Pages; no surprise version bump in the freeze PR itself

---

## Related

- {doc}`design-principles` — ethos, CRS roles, scope
- {doc}`roadmap` — coverage today, direction, non-goals
- {doc}`api/index` — advertised reference
- {doc}`building-docs` — Sphinx / figures
- [AGENTS.md](https://github.com/MatthewJWhittle/tilearray/blob/main/AGENTS.md) · [guidelines.md](https://github.com/MatthewJWhittle/tilearray/blob/main/guidelines.md) · [CONTRIBUTING.md](https://github.com/MatthewJWhittle/tilearray/blob/main/CONTRIBUTING.md)
