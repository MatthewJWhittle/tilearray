# tilearray competitor matrix

**Research date:** 2026-11-03  
**Scope:** Sceptical comparison of alternatives on three real jobs.  
**tilearray claim (baseline):** one `create_array` API for WCS / WMS / WMTS / XYZ → lazy Dask-backed xarray; polite concurrent fetch; optional no-GDAL GeoTIFF via tifffile.

### Jobs

| ID | Job |
|----|-----|
| **A** | WMTS → georeferenced pixel array for analysis (AOI + resolution) |
| **B** | WCS → numerical xarray / GeoTIFF for analysis |
| **C** | Large / repeatable XYZ or WMS mosaic workflow in Python |

Fit scores: **good** / **partial** / **no**.

### Verdict in one paragraph

Most listed tools solve **pieces** of the problem (protocol client, tile math, basemap→GeoTIFF, GDAL virtual raster, analysis on arrays already on disk). The combination “four OGC/tile protocols + AOI/resolution → analysis-ready lazy xarray, without GDAL” is **real differentiation vs the mature mainstream** (OWSLib, contextily, leafmap, GDAL drivers alone). It is **not unique vs emerging peers**: **GeoDataCatalog** markets multi-protocol fetch → xarray with parallel download/cache; the **QGIS Basemap Tile Downloader** covers the same four protocols to GeoTIFF with polite concurrency (desktop, GDAL). For many users with GDAL installed, **GDAL WMTS/WMS + rioxarray** is already good enough for Jobs A and C. Lazy Dask is a genuine advantage for *large* mosaics and pipeline reuse; for one-shot AOIs, a GeoTIFF download often wins.

---

## Summary matrix

| ID | Tool | A | B | C | GDAL? | Dask/xarray native? | Preferable to tilearray when… |
|----|------|---|---|---|---------|---------------------|-------------------------------|
| C01 | OWSLib | partial | partial | no | no | no | You need standards-faithful GetCapabilities / GetTile / GetCoverage plumbing |
| C02 | PyGeoOGC | no | no | good | no* | partial (via pygeoutils) | WMS/REST AOI chunks → GeoTIFF/xarray in hydro stack |
| C03 | GeoDataCatalog | good | good | good | soft (wheels) | yes (xarray; not Dask-first) | YAML multi-source catalog, STAC/WFS/OSM too |
| C04 | GDAL WMTS | good | no | partial | **yes** | via rioxarray | GDAL already in env; need battle-tested WMTS VRT |
| C05 | GDAL WMS | partial | no | good | **yes** | via rioxarray | XYZ/TMS/WMS XML description + cache |
| C06 | contextily | no | no | good | via rasterio | no (numpy→GeoTIFF) | Matplotlib basemap / quick XYZ→GeoTIFF |
| C07 | tms2geotiff | no | no | good | optional (BigTIFF) | no | Simple CLI/GUI XYZ→GeoTIFF |
| C08 | leafmap | no | no | good | often | no | Interactive notebook map + `map_tiles_to_geotiff` |
| C09 | QGIS Basemap Tile Downloader | good | good | good† | **yes** | no | Desktop AOI→GeoTIFF, all 4 protocols, resume/throttle |
| C10 | rioxarray | partial‡ | partial‡ | partial‡ | **yes** (rasterio) | yes (+ optional dask chunks) | Array I/O/CRS/warp on rasters you already have |
| C11 | xarray-spatial | no | no | no | no | yes | Raster *analysis* after you have a DataArray |
| C12 | terrio | no | no | no | no (Rust I/O) | numpy-first | GDAL-free local/remote COG/PMTiles I/O |
| C13 | GRASS r.in.wms | good | no | partial | often | no | GRASS workflow; WMS/WMTS → GRASS raster |
| C14 | mercantile | no | no | no | no | no | XYZ index / bbox→tiles math only |
| C15 | TiTiler | no | no | no | **yes** (rasterio) | server-side | You need to *serve* tiles from COGs/STAC |
| C16 | NASA GIBS examples | partial | no | partial | optional | no | GIBS-specific recipes (OWSLib/GDAL) |

\*PyGeoOGC itself avoids requiring GDAL for WMS fetch; conversion path uses GeoTIFF bytes → xarray.  
†Job C as desktop/plugin, not a Python library API.  
‡Only if you open GDAL WMS/WMTS XML/URLs or files already downloaded.

---

## C01 — OWSLib

**What it does.** Python client for OGC service metadata and request wrappers (WMS, WMTS, WCS, WFS, CSW, WPS, OGC API, …). Returns file-like responses for GetMap / GetTile / GetCoverage; you assemble mosaics and georeferencing yourself.

**Jobs:** A **partial** (can GetTile; no AOI mosaic/georef array) · B **partial** (GetCoverage works when configured; known friction, no lazy xarray) · C **no**.

**GDAL?** No. **Dask/xarray?** No.

**Already good enough / preferable.** Protocol discovery, capabilities parsing, one-off GetCoverage/GetMap when you already know parameters. De-facto standard dependency (PyGeoOGC and many notebooks use it).

**Gaps vs tilearray.** No tiling planner, mosaic, CRS/transform attrs, concurrency policy, or xarray/Dask. WMTS→analysis is exactly where tilearray’s origin story sits (manual tile indices + georef). WCS 2.x GetCoverage UX is a frequent pain point in the wild.

**Maturity.** ~428★ · PyPI `owslib` **0.36.0** (2026-06-04) · **BSD-3-Clause**.

**URLs.** https://github.com/geopython/OWSLib · https://owslib.readthedocs.io · https://pypi.org/project/owslib/

---

## C02 — PyGeoOGC (HyRiver)

**What it does.** High-level clients for ArcGIS REST, **WMS**, and WFS: splits oversized requests, retries/caches, returns bytes or on-disk TIFFs. Documented path: `WMS.getmap_bybox(bbox, resolution)` → `pygeoutils.gtiff2xarray` → xarray.

**Jobs:** A **no** (no WMTS) · B **no** (no WCS) · C **good** for WMS (and REST vectors).

**GDAL?** Not required for the HTTP layer; stack typically uses rasterio/xarray downstream. **Dask/xarray?** Not native; xarray via companion utils after download.

**Already good enough / preferable.** Hydroclimate / NLCD / 3DEP-style **WMS** AOI+resolution workflows inside HyRiver. Better “job-shaped” API than OWSLib for WMS mosaics.

**Gaps vs tilearray.** No WMTS, no WCS, no unified four-protocol API, no first-class lazy Dask mosaic. Depends on OWSLib under the hood for some paths.

**Maturity.** ~13★ · PyPI `pygeoogc` **0.19.5** (2026-10-24) · **MIT** · active HyRiver docs.

**URLs.** https://github.com/hyriver/pygeoogc · https://docs.hyriver.io/readme/pygeoogc.html · https://pypi.org/project/pygeoogc/

---

## C03 — GeoDataCatalog (`geodatacatalog`)

**Correct package:** [geodatacatalog/geodatacatalog](https://github.com/geodatacatalog/geodatacatalog) (not geodatasets, not pyGeoDataCrawler). YAML-configured catalog over WFS/WMS/WMTS/WCS/STAC/OSM/REST/plugins; parallel fetch; disk cache; normalised `GeoDataFrame` / `xarray` / STAC items.

**Jobs:** A **good** · B **good** · C **good** (closest *library* peer overall).

**GDAL?** Soft dependency via geopandas/rasterio/pyogrio wheels (README argues no system GDAL). **Dask/xarray?** xarray yes; architecture is async parallel fetch + cache, not Dask-chunked lazy mosaics as the headline model.

**Already good enough / preferable.** Multi-provider “all data over my AOI” catalogs, French/EU open-data presets, STAC+vector+raster in one `fetch`, Folium explore, zonal stats. If the user’s pain is *catalog orchestration* more than *lazy tile mosaics*, this is broader than tilearray.

**Gaps vs tilearray.** Immature (0★, created ~2026-09); heavier stack; Dask-lazy compute story unclear vs tilearray’s `create_array` + fetch policy; politeness/AIMD framing less explicit than tilearray’s niche docs. Overlap on WMTS/WCS→xarray is high enough that differentiation must be proven on laziness, install weight, and server politeness—not on “common interface” alone.

**Maturity.** **0★** · install from git / local (`uv pip install .`) · **MIT** · very new.

**URLs.** https://github.com/geodatacatalog/geodatacatalog · README claims clone path `tduval/geodatacatalog` aliasing same project.

---

## C04 — GDAL WMTS driver

**What it does.** Opens a WMTS capabilities URL or `<GDAL_WMTS>` XML as a georeferenced GDAL dataset; tiles fetched on read; works with `gdal_translate` / rasterio / rioxarray.

**Jobs:** A **good** · B **no** · C **partial** (WMTS only).

**GDAL?** **Yes.** **Dask/xarray?** Via rioxarray `open_rasterio(..., chunks=...)`.

**Already good enough / preferable.** Anyone with GDAL who wants WMTS as a virtual raster. Mature, widely documented (including NASA GIBS). Preferable when install cost of GDAL is already paid.

**Gaps vs tilearray.** GDAL install/deploy friction (documented user pain); XML/open-options ceremony; weaker unified Python “AOI + resolution + politeness policy” API; WCS not this driver.

**Maturity.** Part of GDAL (industry standard) · docs current in GDAL 3.x.

**URLs.** https://gdal.org/en/stable/drivers/raster/wmts.html

---

## C05 — GDAL WMS driver (XML service description)

**What it does.** Local `<GDAL_WMS>` XML configures WMS, TMS, TiledWMS, VirtualEarth, ArcGIS, etc.; block-based fetch + optional on-disk tile cache; open as a huge virtual raster.

**Jobs:** A **partial** (TMS/WMTS-like via mini-drivers; dedicated WMTS driver is C04) · B **no** · C **good**.

**GDAL?** **Yes.** **Dask/xarray?** Via rioxarray/rasterio.

**Already good enough / preferable.** Production XYZ/WMS mosaics, GIBS TiledWMS examples, offline cache. Often the “serious” answer to Job C in GIS shops.

**Gaps vs tilearray.** XML authoring; GDAL required; not WCS numerical coverages; no tilearray-style fetch policy / soft-fail attrs in Python.

**Maturity.** Core GDAL driver.

**URLs.** https://gdal.org/en/stable/drivers/raster/wms.html

---

## C06 — contextily

**What it does.** Downloads XYZ basemap tiles for Matplotlib (`add_basemap`) or writes a georeferenced GeoTIFF (`bounds2raster`) in EPSG:3857; providers via xyzservices; retries / optional parallel connections.

**Jobs:** A **no** · B **no** · C **good** (XYZ only).

**GDAL?** Indirect (rasterio for write). **Dask/xarray?** No.

**Already good enough / preferable.** Viz basemaps and small/medium XYZ→GeoTIFF. Lower ceremony than tilearray for “give me a background TIFF.”

**Gaps vs tilearray.** No WMTS/WCS/WMS protocol clients; Web Mercator assumption; not analysis-first or lazy xarray; rendered basemap tiles (not numerical coverages).

**Maturity.** ~598★ · PyPI **1.7.1** (2026-07-10) · **BSD-3-Clause**.

**URLs.** https://github.com/geopandas/contextily · https://contextily.readthedocs.io · https://pypi.org/project/contextily/

---

## C07 — tms2geotiff

**What it does.** CLI/GUI script: download TMS/XYZ tiles for an extent/zoom and merge to GeoTIFF (or MBTiles). Pillow + requests/httpx; GDAL optional for BigTIFF.

**Jobs:** A **no** · B **no** · C **good** (XYZ/TMS).

**GDAL?** Optional. **Dask/xarray?** No.

**Already good enough / preferable.** One-shot offline maps; leafmap/samgeo adapted this approach.

**Gaps vs tilearray.** Script not analysis library; no WCS/WMS/WMTS OGC stack; no lazy arrays.

**Maturity.** ~169★ · no formal PyPI package as primary distribution · active enough for niche use.

**URLs.** https://github.com/gumblex/tms2geotiff

---

## C08 — leafmap

**What it does.** Jupyter/interactive mapping swiss-army knife; `add_wms_layer` / XYZ basemaps for display; `map_tiles_to_geotiff` (adapted from tms2geotiff) for download.

**Jobs:** A **no** (display WMTS/WMS ≠ analysis array) · B **no** · C **good**.

**GDAL?** Common in full geospatial installs. **Dask/xarray?** Not for the tile-download path.

**Already good enough / preferable.** Exploratory mapping + quick satellite basemap GeoTIFF in notebooks. Huge audience.

**Gaps vs tilearray.** Download path is XYZ/TMS-centric; not lazy multi-protocol analysis arrays; heavyweight dependency surface if you only need fetch.

**Maturity.** ~3796★ · PyPI **0.64.0** (2026-10-31) · **MIT**.

**URLs.** https://github.com/opengeos/leafmap · https://leafmap.org/notebooks/74_map_tiles_to_geotiff/ · https://pypi.org/project/leafmap/

---

## C09 — QGIS Basemap Tile Downloader

**What it does.** QGIS plugin: auto-detect WMS / WMTS / WCS / XYZ / ArcGIS REST; tile AOI; adaptive throttle; resumable SQLite queue; mosaic to compressed GeoTIFF with overviews (GDAL). WCS kept numerical (native dtype/nodata).

**Jobs:** A **good** · B **good** · C **good** as *desktop* Job C (**no** as importable Python library).

**GDAL?** **Yes** (bundled with QGIS). **Dask/xarray?** No (file output).

**Already good enough / preferable.** Non-developers and analysts who want “draw extent → GeoTIFF” for the **same four protocols** tilearray targets. Feature parity on politeness/resume/WCS fidelity is striking; for file-centric JTBD this can beat a Python library.

**Gaps vs tilearray.** Not a pip-installable API; QGIS runtime; no Dask/xarray; personal/educational ToS framing; ~4★ GitHub but thousands of plugin downloads.

**Maturity.** Plugin **1.13.0** (2026-07-29) · GitHub ~4★ · **GPL-3.0** · https://plugins.qgis.org/plugins/basemap_tile_downloader/

**URLs.** https://github.com/cvonk/qgis-basemap-tile-downloader · QGIS plugins repo above.

---

## C10 — rioxarray

**What it does.** xarray extension over rasterio/GDAL: `open_rasterio`, CRS, clip, reproject, to_raster, optional dask chunks. Not an OGC service client.

**Jobs:** A/B/C **partial** only as the *read* half after GDAL WMS/WMTS or after someone else downloaded GeoTIFF/COG.

**GDAL?** **Yes.** **Dask/xarray?** Yes.

**Already good enough / preferable.** Standard analysis I/O once pixels exist. Pairing with GDAL WMTS/WMS is the incumbent “lazy-ish” stack.

**Gaps vs tilearray.** No create-from-WMTS/WCS/WMS/XYZ helper; GDAL required.

**Maturity.** ~629★ · PyPI **0.24.0** (2026-10-29) · **Apache-2.0**.

**URLs.** https://github.com/corteva/rioxarray · https://pypi.org/project/rioxarray/

---

## C11 — xarray-spatial

**What it does.** 150+ raster analysis ops on xarray (slope, hydrology, multispectral, …); optional GDAL-free GeoTIFF I/O; Numba/Dask/CuPy backends.

**Jobs:** A/B/C **no** (compute, not remote OGC fetch).

**GDAL?** No. **Dask/xarray?** Yes.

**Already good enough / preferable.** Post-fetch analysis—complementary, not competing.

**Gaps vs tilearray.** Orthogonal product; marketing adjacency (“xarray” + “no GDAL”) should not be confused with mosaic fetch.

**Maturity.** ~977★ · PyPI **0.10.20** (2026-10-27) · **MIT**.

**URLs.** https://github.com/xarray-contrib/xarray-spatial · https://xarray-spatial.readthedocs.io

---

## C12 — terrio

**What it does.** Pure-Rust geospatial I/O with Python wheels: GeoTIFF/COG/JP2/PMTiles/MBTiles/STACIT mosaics, windowed remote reads, rasterio-compatible API—**without installing GDAL**.

**Jobs:** A/B/C **no** as OGC WMS/WMTS/WCS clients (reads tile *archives* and COGs, does not speak GetTile/GetMap/GetCoverage to arbitrary services).

**GDAL?** No. **Dask/xarray?** NumPy-oriented; not tilearray’s Dask mosaic model.

**Already good enough / preferable.** GDAL-free local/remote raster I/O—validates “no GDAL” demand in the ecosystem, but for file/COG access not service mosaicking.

**Gaps vs tilearray.** Different layer of the stack; complementary if tilearray writes COGs that terrio reads.

**Maturity.** ~254★ · PyPI **0.6.1** (2026-11-02) · **MIT AND MPL-2.0** · very fast-moving (new 2026).

**URLs.** https://github.com/opengeos/terrio · https://terrio.dev/

---

## C13 — GRASS r.in.wms

**What it does.** Downloads WMS/WMTS into a GRASS raster: tiles by region, reprojects, patches. Drivers include WMS_GRASS, WMTS_GRASS, WMS_GDAL.

**Jobs:** A **good** (WMTS→GRASS raster) · B **no** · C **partial** (WMS yes; GRASS-centric, not general Python).

**GDAL?** Often (gdalwarp / WMS_GDAL). **Dask/xarray?** No.

**Already good enough / preferable.** GRASS/QGIS Processing users needing WMS/WMTS import.

**Gaps vs tilearray.** GRASS session/region model; not pip library; no WCS; no xarray.

**Maturity.** Part of GRASS GIS (decades).

**URLs.** https://grass.osgeo.org/grass-stable/manuals/r.in.wms.html

---

## C14 — mercantile

**What it does.** Web Mercator XYZ helpers: `tile`, `bounds`, `tiles(bbox, zooms)`, quadkeys, CLI.

**Jobs:** A/B/C **no** (building block only).

**GDAL?** No. **Dask/xarray?** No.

**Already good enough / preferable.** When you only need tile index math (morecantile if non-Mercator TMS).

**Gaps vs tilearray.** No HTTP, decode, mosaic, or arrays.

**Maturity.** ~452★ · PyPI **1.2.1** (2021-04-21; stable/maintenance) · **BSD**.

**URLs.** https://github.com/mapbox/mercantile · https://mercantile.readthedocs.io

---

## C15 — TiTiler

**What it does.** FastAPI dynamic tile **server** for COG/STAC/MosaicJSON/xarray backends; emits XYZ/WMTS for maps. Inverse of “consume tiles into an array.”

**Jobs:** A/B/C **no** (unless you misuse it as a client against yourself).

**GDAL?** Yes (rasterio). **Dask/xarray?** Server can tile xarray/Zarr sources.

**Already good enough / preferable.** Publishing analysis-ready rasters as tiles.

**Gaps vs tilearray.** Opposite direction in the pipeline.

**Maturity.** ~1201★ · PyPI meta **0.25.0** (2026-06-06) · **MIT**.

**URLs.** https://github.com/developmentseed/titiler · https://developmentseed.org/titiler/

---

## C16 — NASA GIBS Python examples

**What it does.** Official notebooks/docs: OWSLib WMS GetMap → GeoTIFF; WMTS capabilities; GDAL WMTS/WMS XML recipes for GIBS layers. Not a reusable library.

**Jobs:** A **partial** · B **no** · C **partial** (GIBS-specific).

**GDAL?** Optional in examples. **Dask/xarray?** No (rasterio open after download).

**Already good enough / preferable.** GIBS-only users following first-party recipes.

**Gaps vs tilearray.** No general AOI mosaic library; WMTS time limitations called out in GDAL path; still OWSLib/GDAL complexity.

**Related:** [spaceml-org/GIBS-Downloader](https://github.com/spaceml-org/GIBS-Downloader) — GIBS CLI for ML pipelines (GDAL required); not general OGC.

**URLs.** https://nasa-gibs.github.io/gibs-api-docs/python-usage/ · https://nasa-gibs.github.io/gibs-api-docs/map-library-usage/

---

## Additional alternatives discovered

| Tool | Why it matters | Jobs | URLs |
|------|----------------|------|------|
| **rasterama** | Headless XYZ→GeoTIFF/BigTIFF via tifffile.memmap; retries/rate limit; no GUI | C good | https://pypi.org/project/rasterama/ |
| **samgeo `tms_to_geotiff`** | Same family as leafmap/tms2geotiff inside segment-geospatial | C good | https://github.com/opengeos/segment-geospatial |
| **wms-scraper** | CLI WMS/WMTS scrap → GeoTIFF mosaic (GDAL) | A partial / C good | https://github.com/GeoHistoricalData/wms-scraper |
| **tiles-to-tiff** | Small XYZ→GeoTIFF script (GDAL) | C partial | https://github.com/jimutt/tiles-to-tiff |
| **morecantile** | mercantile successor for arbitrary TileMatrixSets (used by rio-tiler/TiTiler) | building block | https://github.com/developmentseed/morecantile |
| **rio-tiler** | Read windows/tiles from COG/STAC (server-side tiling), not OGC download | no for A/B/C fetch | https://cogeotiff.github.io/rio-tiler/ |
| **pyows** | WMS/WCS request encode/decode (server/client helper); not mosaic | partial protocol | https://github.com/EOxServer/pyows |
| **RSGISLib wmts_tools** | `get_wmts_as_img` via OWSLib+GDAL for ROI | A partial | https://rsgislib.org/rsgislib_tools_wmts_tools.html |
| **odc-stac** | STAC → lazy xarray (common modern path; not WMS/WMTS) | adjacent to B | https://github.com/opendatacube/odc-stac |
| **geodatasets** | Example spatial file catalog—**not** a competitor (name collision risk with C03) | no | https://github.com/geopandas/geodatasets |

Search queries used: `python wmts download mosaic xarray`, `wcs to xarray python`, `xyz tiles to geotiff python`, `owslib alternative`.

---

## Differentiation critique (marketing vs real)

| Claim | Assessment |
|-------|------------|
| Common interface for 4 protocols | **Mostly genuine** vs OWSLib/contextily/leafmap/GDAL-one-driver-at-a-time. **Weakened** by GeoDataCatalog (multi-protocol→xarray) and QGIS Basemap Tile Downloader (same 4 protocols→GeoTIFF). |
| Lazy Dask-backed xarray | **Genuine** vs download-first tools (contextily, tms2geotiff, QGIS plugin, most GIBS recipes). **Incumbent alternative:** GDAL WMTS/WMS + rioxarray chunks (GDAL tax). GeoDataCatalog returns xarray but is not clearly Dask-lazy mosaics. |
| Polite concurrent fetch | **Shared** with PyGeoOGC, contextily (`n_connections`), GeoDataCatalog, QGIS plugin (adaptive throttle). tilearray must win on *defaults + policy transparency*, not on the idea alone. |
| Optional no-GDAL | **Real for some deployers** (P14–P16 style pain). Counterweights: many target users already have GDAL via conda/QGIS; terrio/xarray-spatial show broader “no GDAL” movement for *I/O/analysis*, not service fetch. |
| WCS numerical path | **Important** vs WMS/XYZ basemap tools that only give rendered RGB. Shared with OWSLib (painful), QGIS plugin, GeoDataCatalog SoilGrids-style WCS. |

**When file download beats lazy xarray:** one AOI, fits in memory, need QGIS/ArcGIS next, or checkpoint/resume on flaky networks (QGIS plugin cache/queue). **When lazy xarray wins:** many tiles, selective compute, multi-AOI pipelines, cluster/Dask, avoid writing huge intermediate GeoTIFFs.

---

## Closest peers (priority watchlist)

1. **GeoDataCatalog** — same conceptual niche in Python (multi-OGC → xarray); monitor maturity.  
2. **QGIS Basemap Tile Downloader** — same protocols + politeness for desktop GeoTIFF JTBD.  
3. **GDAL WMTS/WMS + rioxarray** — default answer wherever GDAL is free.  
4. **PyGeoOGC** — best existing *library* for WMS AOI+resolution → array-ish workflows.  
5. **contextily / leafmap / rasterama / tms2geotiff** — XYZ Job C incumbents.

---

## Source confidence

Official docs/repos/PyPI verified for C01–C02, C04–C16 and GeoDataCatalog README (2026-11-03). Star counts from GitHub search API same day; PyPI versions via Warehouse JSON. C03 identity confirmed as `geodatacatalog/geodatacatalog` after rejecting geodatasets / geodatacrawler name collisions.
