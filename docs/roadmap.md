# Roadmap

tilearray turns remote tile services into Dask-backed xarray arrays for **geo data engineers**: a large AOI becomes a polite fetch-and-assemble problem, with clear memory / concurrency / lazy-vs-cache behaviour and the **same API** from a laptop to a larger machine.

**Today (0.3.x, Alpha):** one `create_array` entry point for WCS, WMS, WMTS, and XYZ; presets for OSM XYZ and EA DSP WCS; documented paths for USGS 3DEP WCS, GIBS XYZ/WMTS, Kartverket WMTS (EPSG:25833), EA VOM WMS, and keyed OS Maps XYZ. The package is Alpha until a stable 1.0 contract.

## Protocol & host coverage (today)

| Protocol | Depth | Version lock-in |
|----------|-------|-----------------|
| **WCS** | Deep (capabilities, planning, GeoTIFF, EA + USGS evidence) | **2.0.x only** — no WCS 1.x chase |
| **XYZ** | Medium–deep (OSM preset, GIBS-as-XYZ guide) | OSM slippy Y; no TMS Y-flip |
| **WMTS** | Medium (REST/KVP + caps-driven native matrices; GIBS + Kartverket guide) | **1.0.0** |
| **WMS** | Thin (GetMap + quickstart recipe) | Teaching path **1.3.0** |

New host presets and deeper WMTS/WMS guides are the next theme after the shipped core — not a catalogue chase.

## Direction

**Scale & hosts** — big AOI on more real hosts without a second API: USGS 3DEP WCS preset, NASA GIBS as first-class XYZ and/or native WMTS + visual guide, EA VOM WMS guide, keyed XYZ pattern (env/`params`; no secrets in repo), guidance for scaling compute without raising host concurrency past the ceiling, and stronger WMS/WMTS contract coverage.

**Stable contract (1.0)** — freeze the public API users already rely on; graduate from Alpha; harden tests and supported Python matrix; deeper API reference.

Product boundaries and CRS vocabulary: {doc}`design-principles`.

## Non-goals (through ~0.3)

1. **STAC / COG cube builder** — leave to stackstac / odc-stac / rioxarray; interoperate, don’t duplicate.
2. **Map SDK or tile server** — no Leaflet/Cesium UI; no OWS-style serving.
3. **Async-first rewrite** — sync httpx + Dask threads stays the laptop-friendly model unless evidence forces otherwise.
4. **GDAL on every path** — optional extra later is fine; basic XYZ should not require full GDAL.
5. **Universal host coverage / ToS-grey demos** — no Google-Satellite-first docs; presets only where politeness and licensing are clear.
6. **Full temporal datacube API** — year/layer switching stays recipe-level until the spatial large-AOI path is solid.
7. **WCS 1.x full stack** — defer unless a named must-have host appears.
8. **OAuth / short-lived token refresh** — keyed hosts use env-based keys/headers only for now.

## See also

- {doc}`how-it-works` — plan → fetch → decode → mosaic
- {doc}`polite-fetch` — host ceilings, soft-fail, scaling
- {doc}`example-sources` — public endpoints and presets
