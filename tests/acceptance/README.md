# Community-derived acceptance suite (#97)

Offline scenarios that reproduce **public** WMTS / WCS / WMS user problems
against tilearray. This folder is an evidence / fixture harness — product fixes
belong on the owning GitHub issues listed in [`catalog.py`](catalog.py).

## Status vocabulary

| Status | Meaning |
|--------|---------|
| `pass` | Current public API already meets the required offline behaviour |
| `fail` | API runs but georef / pixels / requests are wrong |
| `unsupported` | Required CRS / capability cannot be expressed yet |
| `user_config_error` | Invalid caller input; early rejection expected |
| `correct_rejection` | Library correctly refuses an unsupported promise |
| `not_reproduced` | Not exercised here (optional live only) |

`fail` / `unsupported` are **baseline classifications**, not automatic
regression labels. Owning issues promote a scenario to a hard CI assertion when
the fix lands.

## Scenario index

| ID | Baseline | Owning issues | Fixture |
|----|----------|---------------|---------|
| S1 NPI WMTS EPSG:25833 | unsupported | #92 #93 | `fixtures/s1_npi_wmts/` |
| S2 WGS84 WMTS georef | fail | #93 #60 | `fixtures/s2_wgs84_wmts/` |
| S3 Multi-bbox WMTS loop | pass | #87 #61 | `fixtures/s3_multi_bbox/` |
| S4 Clipped WCS response | fail | #95 #94 | `fixtures/s4_wcs_clipped/` |
| S5 WCS 2.0.1 subsets | unsupported | #92 #96 #88 | `fixtures/s5_wcs_201/` |
| S6 EPGS typo / PDOK | user_config_error | #92 #88 #96 | `fixtures/s6_pdok_wms/` |
| S7 Max-dim WCS assemble | pass | #61 #88 #50 | `fixtures/s7_soilgrids_wcs/` |
| S8 Raster semantics | pass | #94 | `fixtures/s8_raster_semantics/` |
| S9 Throttling / soft-fail | pass | #61 #59 #65 | `fixtures/s9_resilience/` |

Machine-readable detail (provenance, sample parameters, expected outcomes) lives
in `catalog.py`. Golden WMTS maths that must stay independent of library
planning code live in `golden.py`.

## Markers

Default PR CI stays network-free (`not slow and not net`).

- Offline acceptance tests: `@pytest.mark.unit` (and sometimes `@pytest.mark.contract` for recorded XML).
- Optional live smoke (when added): `@pytest.mark.net` + `@pytest.mark.slow` — never bulk OSM scrape; respect provider terms.

## Non-goals

No competitor benchmarking, no STAC/COG scope, no secrets, no “Problems from
the field” Sphinx page until claims are verified end-to-end.
