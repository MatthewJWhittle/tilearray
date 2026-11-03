# User segments and jobs-to-be-done

**Research date:** 2026-11-03  
**Stance:** sceptical — segments ranked by verified first-hand density, not by wishful TAM.  
**Evidence levels:** 1 experienced problem · 2 wanted a tool · 3 interest in tilearray · 4 used tilearray. **No level 3/4 found.**

---

## Top segments (2–3)

### 1. Environmental / hydro / soil analysts (Python) — **best first users**

| | |
|--|--|
| **JTBD** | “When a public WCS only answers small subsets (or OWSLib fights me), help me pull an AOI of **numerical** elevation/soil/climate grids into analysis without hand-rolling tile loops.” |
| **Jobs** | Primarily **B** (WCS→array); sometimes **A** when only WMTS exists. |
| **Evidence for** | P06/N05 (3DEP → xarray; tile + `rasterio.merge`); P07/N06/N07 (SoilGrids WCS pain); N24/N25 (GeoMet/MAAP WCS tutorials); README EA Lidar recipe matches this persona. |
| **Evidence against** | N08 ISRIC docs call OWSLib “very convenient” for small AOIs; many succeed after CRS/resolution education (N07); end artefact is often a **GeoTIFF file**, not a lazy Dask graph (P06). |
| **tilearray fit** | Strongest product–problem match when server caps force multi-request mosaics and the user already lives in xarray. Must beat a 20-line OWSLib + rasterio script on clarity, politeness, and failure modes. |
| **Adoption barrier** | Alpha API risk; discoverability (0 stars); “why not GDAL/rioxarray?” if conda already works. |

### 2. Pipeline / automation GIS developers — **strong problem, file-shaped demand**

| | |
|--|--|
| **JTBD** | “When I need hundreds–tens of thousands of AOI patches from WMTS/WMS/XYZ caches, automate bbox→tiles→georeferenced mosaic with retries and throttle.” |
| **Jobs** | **A** and **C**; deliverable often GeoTIFF for QGIS/ArcGIS/ML. |
| **Evidence for** | P02 (10k WMTS patches); P08 (city WMS tile+merge); P09 (ArcGIS XYZ→GeoTIFF; names tms2geotiff/leafmap/FME); N11–N13 scrapers/plugins; N15 rasterama. |
| **Evidence against** | Desktop plugin C09 already covers four protocols with resume/throttle; leafmap/tms2geotiff/contextily cover XYZ; GeoServer cascade accepted on P02; Pangeo prefers COG/STAC for analysis (N20–N21). |
| **tilearray fit** | Credible if the pipeline is **Python-native** and wants one API across WMS/WMTS/WCS/XYZ plus shared fetch policy. Weak if the user only needs XYZ→GeoTIFF once. |
| **Adoption barrier** | Incumbents named in the wild; lazy xarray is optional for “write a TIFF and move on.” |

### 3. CV / ML patch assemblers — **secondary; GeoTIFF-first**

| | |
|--|--|
| **JTBD** | “Give me basemap/imagery chips as GeoTIFF/tensors for SAM or training.” |
| **Jobs** | **C** (XYZ/TMS); rarely true WCS. |
| **Evidence for** | leafmap/`tms_to_geotiff` (N16); P09 tool-seeking. |
| **Evidence against** | File-on-disk is the interface; rendered RGB tiles; OSM bulk policy (G01) limits demos; not OGC-protocol pain. |
| **tilearray fit** | Only if multi-protocol + Dask sampling chips beats leafmap. Unlikely as beachhead. |

### Deprioritised segments

| Segment | Why not first |
|---------|----------------|
| Desktop GIS offline downloaders | C09 / QGIS Save-as; Python library won’t displace GUI (P02, P08, N13). |
| Remote-sensing viz (GIBS/PyGMT) | Vendor/GDAL recipes “good enough” (N10, N22, N23). |
| Pangeo cloud-native analysts | Moving to COG/Zarr/STAC away from OGC mosaics (N20–N21). |
| “Anyone who hates GDAL install” | P14–P16 are env binding pains, not mosaicking JTBD; conda/OSGeo Docker exist (G02, P16). |

---

## Jobs A / B / C — when tilearray wins vs when files win

| Job | Description | When lazy Dask/xarray wins | When GeoTIFF download wins |
|-----|-------------|----------------------------|----------------------------|
| **A** | WMTS → georeferenced pixels (AOI + resolution) | Large mosaics, selective compute, reuse graph | One AOI, QGIS next, GDAL WMTS already installed (C04, N10) |
| **B** | WCS → numerical xarray | Server max size forces many GetCoverage; want shared politeness + soft-fail | Small subset; OWSLib + open with rioxarray (N08, N24, N25); P06 already used rasterio.merge |
| **C** | Large/repeatable XYZ/WMS workflow | Multi-host pipelines, AIMD/circuit breaker, cache_dir, compute_with_policy | contextily/leafmap/tms2geotiff/rasterama/QGIS plugin for one-shot files |

**Rendered vs numerical (G03, P10, P11):** WMS/WMTS/XYZ mosaics remain pictures. Analytical claims belong primarily with **WCS** (and native rasters). Promoting “analysis-ready” from basemap tiles without that caveat will create false expectations (P10).

---

## Hypotheses vs segment evidence

| Hypothesis | Segment verdict |
|------------|-----------------|
| Devs frequently write custom mosaic code | **Supported** (P02, P06, N11–N13) — but many stop at scripts/plugins. |
| OGC tools too low-level | **Partially** — OWSLib footguns real (P03, P04, N09); GDAL often “fine once learned” (N10). |
| Lazy Dask/xarray advantage | **Unproven demand** — level-1/2 asks are usually files; laziness is a bet. |
| Common multi-protocol interface | **Useful** vs OWSLib alone; **weakened** by C03 GeoDataCatalog + C09 plugin. |
| Avoiding GDAL eases deploy | Soft nice-to-have; **not** primary JTBD from P14–P16. |
| Concurrency/retries valuable | **Supported** across scrapers, PyGeoOGC, C09 — shared idea, not unique. |
| Env analysts / RS / CV early adopters | Env **yes**; RS **maybe** (GDAL incumbency); CV **file tools first**. |
