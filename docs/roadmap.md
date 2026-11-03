# Roadmap

tilearray turns remote tile services into Dask-backed xarray arrays for **geo data engineers**: a large AOI becomes a polite fetch-and-assemble problem, with a clear memory / concurrency / lazy-vs-cache story and the **same API** from a laptop to a larger machine.

We prefer **fewer, meaningful releases**. Work lands on `main` continuously; PyPI is cut when a theme’s exit criteria clear — not as feature drip.

```text
  0.1.x (shipped)          0.2                      0.3                         1.0
  open alpha proof    →    trustworthy core    →    scale + hosts          →    stable contract
```

Tracking lives in GitHub milestones and a small set of thematic issues — not one ticket per checklist cell.

## Protocol & host coverage (today)

The four-protocol pitch is **real but uneven**:

| Protocol | Depth | Version lock-in |
|----------|-------|-----------------|
| **WCS** | Deep (capabilities, planning, GeoTIFF, EA + USGS evidence) | **2.0.x only** — no WCS 1.x chase |
| **XYZ** | Medium–deep (OSM preset, GIBS-as-XYZ guide) | OSM slippy Y; no TMS Y-flip |
| **WMTS** | Medium–thin (REST/KVP + optional caps; no guide yet) | **1.0.0** |
| **WMS** | Thin (GetMap + quickstart recipe) | Teaching path **1.3.0** |

**Hosts in short:** two presets (OSM XYZ, EA DSP WCS); documented recipes for USGS 3DEP WCS, GIBS XYZ, EA VOM WMS, and keyed OS Maps XYZ. Live nightly confidence is still EA-shaped. New host presets stay in **0.3** — 0.2 keeps the trustworthy core.

Recent reliability fixes on `main` (no patch release required): sub-pixel WCS planning ([#50](https://github.com/MatthewJWhittle/tilearray/issues/50) / [#66](https://github.com/MatthewJWhittle/tilearray/pull/66)) and shared-fetcher concurrency ([#64](https://github.com/MatthewJWhittle/tilearray/issues/64) / [#67](https://github.com/MatthewJWhittle/tilearray/pull/67)).

## 0.2 — Trustworthy core

Make the installed library boringly reliable on the documented paths: fix objective-blocking engineering debt **without** expanding the protocol surface or shipping new host presets.

- Replace or quarantine the GeoTIFF decode spine (drop the fragile zarr pin cage)
- Analysis-ready CRS / attrs and a documented rioxarray handoff
- One canonical compute habit and a fresh stranger first-look pass
- Honest “WMTS supported via `WMTSConfig`, guide TBD” so the four-protocol claim is not oversold
- Large-AOI politeness evidence / nightly soak under presets
- Start HTTP consolidation (capabilities → httpx or one UA/timeout policy) — caps/DescribeCoverage on httpx with shared UA/timeout + config credential parity (#57)

**Exit criteria for tagging 0.2.0**

- [ ] Clean-env `pip install tilearray` + WCS GeoTIFF + XYZ quickstarts succeed without pin gymnastics
- [ ] Documented attrs/CRS story sufficient for xarray → plot / simple rioxarray on EA + GIBS/OSM examples
- [ ] Fresh stranger first-look: install → mosaic succeeds; no contradictory compute guidance on primary paths
- [ ] CHANGELOG theme is coherent; attestations appear on this (or the immediately prior) PyPI artifact
- [ ] No known silent mosaic corruption on documented RGB / single-band GeoTIFF paths

Milestone: [0.2 Trustworthy core](https://github.com/MatthewJWhittle/tilearray/milestone/1)

## 0.3 — Scale & hosts

Big AOI on more real hosts, safely — including “larger machine” without a second API. Host work follows a ranked pack (not a catalogue chase):

1. **USGS 3DEP WCS** preset (cassette-proven multipart path) — [#58](https://github.com/MatthewJWhittle/tilearray/issues/58)
2. **NASA GIBS** as first-class XYZ and/or native WMTS + visual guide — [#58](https://github.com/MatthewJWhittle/tilearray/issues/58), [#60](https://github.com/MatthewJWhittle/tilearray/issues/60)
3. **EA VOM WMS** visual guide / first-class recipe (completes the UK EA story)
4. **Keyed XYZ pattern** (OS Maps–class `key=` from env/`params`; no secrets in repo)

Also in theme: larger-machine “don’t stampede” guidance, AOI/grid ergonomics, WMTS contract tests (pin primary live host to GIBS), and decode upgrades (e.g. palette→RGB).

**Exit criteria for tagging 0.3.0**

- [ ] ≥1 additional WCS (**USGS 3DEP** preferred) and ≥1 WMTS/WMS host (**GIBS WMTS** and/or **EA VOM WMS**) with preset or first-class recipe + visual guide
- [ ] Keyed-XYZ auth pattern documented without committed secrets
- [ ] Written + tested guidance for scaling beyond a laptop that preserves host ceilings
- [ ] Large-AOI soak evidence (hundreds–thousands of tiles on at least one preset host) in docs or benchmarks
- [ ] WMS/WMTS contract coverage no longer the weak sibling of WCS

Milestone: [0.3 Scale & hosts](https://github.com/MatthewJWhittle/tilearray/milestone/2)

## 1.0 — Stable contract

Freeze the public API users already rely on; graduate from Alpha.

- Public API freeze / Alpha graduation checklist
- Hardening (property tests, stronger nightly, supported Python matrix)
- Docs maturity (deeper API reference; optional versioned docs later)

**Exit criteria for tagging 1.0.0**

- [ ] Stability guarantee published; Alpha badge removed
- [ ] North-star success criteria met on supported Python versions
- [ ] No S0 gaps open; S1 gaps closed or explicitly accepted in docs as known limits
- [ ] Provenance/attestations on the 1.0 artifact; release checklist boring

Milestone: [1.0 Stable contract](https://github.com/MatthewJWhittle/tilearray/milestone/3)

## Non-goals (through ~0.3)

1. **STAC / COG cube builder** — leave to stackstac / odc-stac / rioxarray; interoperate, don’t duplicate.
2. **Map SDK or tile server** — no Leaflet/Cesium UI; no OWS-style serving.
3. **Async-first rewrite** — sync httpx + Dask threads stays the laptop-friendly model unless evidence forces otherwise.
4. **GDAL on every path** — optional extra later is fine; basic XYZ should not require full GDAL.
5. **Many tiny PyPI releases** — batch on `main`; cut when a theme exits.
6. **Universal host coverage / ToS-grey demos** — no Google-Satellite-first docs; presets only where politeness and licensing are clear.
7. **Full temporal datacube API** — year/layer switching stays recipe-level until the spatial large-AOI story is solid.
8. **Rewriting Sphinx onto another docs stack** — the usage-guides pipeline stays unless it fails the visual/live-host preference.
9. **WCS 1.x full stack** — defer unless a named must-have host appears.
10. **OAuth / short-lived token refresh** — 0.3 auth is env-based keys/headers only.

## See also

- {doc}`design-principles` — canonical ethos, CRS roles, scope/non-goals
- {doc}`how-it-works` — plan → polite fetch → mosaic
- {doc}`example-sources` — public endpoints and presets
- {doc}`building-docs` — maintainer notes for Sphinx and figures
- [GitHub milestones](https://github.com/MatthewJWhittle/tilearray/milestones)
- [Open issues](https://github.com/MatthewJWhittle/tilearray/issues)
