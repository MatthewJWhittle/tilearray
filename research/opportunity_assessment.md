# Opportunity assessment — tilearray

**Date:** 2026-11-03  
**Question:** Does tilearray solve a sufficiently common and painful problem for Python geospatial developers, and is its approach meaningfully better than alternatives?  
**Method:** Original sources (P/N/C/G/D), docs/README skim, evidence levels kept separate. No app code changes; no public posts; no bulk tile fetches.

---

## Executive verdict

**The underlying problem is real (evidence level 1–2). Demand specifically for tilearray is not demonstrated (no level 3–4).**  
AOI mosaicking from WMTS/WMS/XYZ and chunked WCS→array show up repeatedly. Mature alternatives already cover most “download a GeoTIFF” jobs. tilearray’s plausible wedge is **Python multi-protocol → lazy Dask xarray with explicit fetch politeness**, especially **WCS under server size caps** for env/hydro analysts — not “nobody can mosaic tiles.” Promote **narrow validation asks**, not broad marketing, until external users try it.

---

## Working hypotheses — keep / kill / modify

| # | Hypothesis | Verdict | Why |
|---|------------|---------|-----|
| 1 | Devs write custom mosaic code | **Keep** | P02, P06, N11–N13 |
| 2 | OGC tools too low-level for analysis | **Modify** | OWSLib friction real (P03–P04, N09); GDAL often adequate (N10, N22–N23); SoilGrids docs praise OWSLib (N08) |
| 3 | Lazy Dask/xarray beats GeoTIFF download | **Unproven** | Complainants usually want files (P06, P09, C07–C09); laziness is differentiation *if* large AOI / pipelines validate it |
| 4 | Common interface reduces complexity | **Keep with caveat** | Genuine vs OWSLib alone; peers C03 + C09 weaken uniqueness |
| 5 | Avoiding GDAL eases install | **Soft / secondary** | G02 + P14–P16 show install pain; not mosaicking JTBD; conda/Docker exist |
| 6 | Configurable concurrency/retries valuable | **Keep** | Shared with PyGeoOGC, contextily, C09 — compete on defaults/UX |
| 7 | Env / RS / CV early adopters | **Modify** | Env/hydro strongest; RS GDAL-incumbent; CV file-first |

---

## Differentiation (sceptical)

**Genuine vs mainstream:** one `create_array` for WCS/WMS/WMTS/XYZ → Dask-backed xarray; shared decode; AIMD/circuit-breaker presets; optional tifffile GeoTIFF path (docs/README).

**Not unique / already good enough:**

- **GDAL WMTS/WMS + rioxarray** for anyone who already has GDAL (C04–C05, N10).
- **contextily / leafmap / tms2geotiff / rasterama** for XYZ→GeoTIFF (C06–C08, N15–N17).
- **PyGeoOGC** for WMS AOI→array-ish hydro workflows (C02).
- **QGIS Basemap Tile Downloader** for the same four protocols → GeoTIFF with throttle/resume (C09).
- **GeoDataCatalog** (2026-09, 0★) for YAML multi-OGC → xarray — conceptual peer (C03).
- **OWSLib + rasterio.merge** already solved P06.

**Must not overclaim:** mosaicking WMS/WMTS/XYZ cannot restore numerical values lost to rendering (P10, G03). OSM bulk mosaics violate tile policy (G01).

---

## Opportunity by job

| Job | Opportunity | Risk |
|-----|-------------|------|
| **B WCS→xarray** | Best wedge; matches origin story and P06 | Tutorials show file path works; alpha trust |
| **A WMTS georef array** | Real pain (P01–P02) | Accepted answers push WCS or GeoServer; GDAL WMTS |
| **C XYZ/WMS repeatable** | Crowded | leafmap/contextily/C09 win on familiarity |

---

## What would disprove product value?

1. Target users try tilearray and still prefer OWSLib + rasterio / GDAL WMS XML / leafmap for the same AOI.  
2. External interviews say the missing piece is **resume/queue to GeoTIFF** (C09) or **STAC/COG**, not lazy OGC mosaics.  
3. GeoDataCatalog (or similar) matures into the default multi-protocol→xarray client.  
4. Hosted analysis data moves entirely to COG/Zarr so OGC mosaic clients shrink (N20–N21).  
5. Zero level-3 interest after several well-targeted community asks (see validation plan).

---

## Promote now?

**No broad promotion.** Ship **low-effort validation** (draft posts in `community_validation_plan.md` — do not publish from this research pass). Stabilize alpha trust (errors, politeness, docs honesty on rendered vs numerical) before showcases. r/Python cold showcase of a near-zero-adoption alpha is likely net-negative (D03).

---

## Simplest external feedback without more features

1. 3–5 DMs/calls with people like P06 (WCS tile+merge → xarray): “Would lazy `create_array` beat your script?”  
2. One Pangeo or GIS SE **question** about current WMTS/WCS→xarray practice (not a product pitch).  
3. Offer a short notebook against a **non-OSM** public WCS (e.g. EA Lidar) and ask for reactions — no bulk OSM (G01).

---

## Evidence level tally (this research)

| Level | Finding |
|-------|---------|
| 1 | Multiple independent reports of mosaic / OWSLib / WCS-limit pain |
| 2 | Scrapers, plugins, explicit tool-seeking (P09, N11–N13, C09) |
| 3 | **None** for tilearray |
| 4 | **None** (author surfaces only; GitHub 0★ / 0 forks as of 2026-11-03) |
