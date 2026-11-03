# Executive summary — tilearray user research

**Date:** 2026-11-03  
**Repo:** https://github.com/MatthewJWhittle/tilearray  
**Docs skimmed:** https://matthewjwhittle.github.io/tilearray/ (+ how-it-works, README on main)  
**Stance:** sceptical product research; evidence levels separated; counterexamples sought.

---

## Synthesis answers

### Does it solve a real problem?

**Yes, at evidence levels 1–2.** Independent users struggle with WMTS georeferencing and bbox automation (P01–P02), WCS server size limits on the way to xarray (P06), and city-scale WMS/XYZ tile merges (P08–P09). **No evidence levels 3–4:** nobody outside author channels expressed interest in or successfully used tilearray (GitHub 0★/0 forks; no third-party mentions found).

### Most likely first user?

**Environmental / hydro / soil analysts in Python** who already hit WCS caps and mosaic with OWSLib + rasterio (P06, SoilGrids/3DEP/EA-shaped workflows). Second: pipeline engineers automating AOI downloads — but they often want **GeoTIFF**, where leafmap/tms2geotiff/QGIS plugins win.

### Genuine differentiation?

**Partially.** Multi-protocol `create_array` → lazy Dask xarray + shared polite fetch is real versus OWSLib, contextily, and one-off scripts. It is **not** unique versus **GeoDataCatalog** (multi-OGC→xarray, also immature) or **QGIS Basemap Tile Downloader** (same four protocols→GeoTIFF). Against GDAL-equipped shops, **GDAL WMTS/WMS + rioxarray** remains the default.

### Already exists?

**Pieces do.** Protocol clients (OWSLib), WMS AOI libraries (PyGeoOGC), XYZ downloaders (contextily/leafmap/tms2geotiff/rasterama), GDAL drivers, desktop mosaickers (C09), analysis I/O (rioxarray). tilearray’s bet is the **composition** (four protocols + lazy array + politeness), not a greenfield category.

### Is GDAL-free important?

**Secondary.** Upstream and Reddit show install pain (G02, P14–P16), but those threads are about env binding, not tile mosaicking. Many targets already have GDAL via conda/QGIS/Docker. Treat no-GDAL as a deploy nicety, not the core JTBD.

### What would disprove value?

Target users try it and keep OWSLib+rasterio / GDAL XML / leafmap; interviews show demand is resume-to-GeoTIFF or STAC/COG instead; a peer library becomes the default multi-protocol→xarray client; or community asks yield zero level-3 interest.

### Promote now?

**No.** Run the low-effort validation plan (drafts only in-repo). Stabilize honesty on rendered vs numerical tiles (P10, G03) and ToS (G01). Avoid cold r/Python showcases.

### Simplest external feedback without more feature work?

Five personal outreach conversations with WCS-tiling practitioners + one careful Pangeo/GIS-SE question about current practice (see `community_validation_plan.md`). Offer an EA Lidar notebook; do not bulk-fetch OSM.

---

## Hypothesis scorecard

| Hypothesis | Result |
|------------|--------|
| Custom mosaic code is common | Supported |
| OGC tools too low-level | Partial — friction real; many happy paths exist |
| Lazy Dask/xarray advantage | Unproven as user demand |
| Common interface value | Supported vs OWSLib; weakened by peers |
| GDAL-free value | Soft secondary |
| Concurrency/retries value | Supported but not unique |
| Env/RS/CV early adopters | Env yes; RS/CV weaker |

---

## Artefacts in this PR

| File | Role |
|------|------|
| `research/evidence_matrix.csv` | Independent user reports + levels |
| `research/competitor_matrix.md` | C01–C16 + peers |
| `research/user_segments_and_jtbd.md` | Segments + jobs A/B/C |
| `research/opportunity_assessment.md` | Critical opportunity write-up |
| `research/community_validation_plan.md` | Draft posts (unpublished) |
| `research/source_log.csv` | Source verification log |
| `research/executive_summary.md` | This file |

**Caveat:** Reddit P08–P12, P14–P16 were blocked (HTTP 403) in this environment; those rows are marked `partial` in the evidence/source logs and should be re-fetched before public citation.
