# Community validation plan (drafts only — **do not publish**)

**Constraint:** Research assignment only. No public posts from this work. Drafts below are for a human maintainer to edit and post later if they choose.  
**Goal:** Obtain first **level 3** signals (interest in trying) and ideally **level 4** (successful use) without new features.  
**OSM:** Do not validate with bulk downloads against `tile.openstreetmap.org` (G01). Prefer documented public WCS (e.g. EA Lidar) or provider-permitted XYZ.

---

## Principles

1. Lead with **their** workflow pain, not a product pitch.  
2. Disclose affiliation if recommending tilearray.  
3. Prefer communities with GIS/Python overlap over general showcase spam.  
4. One ask per venue; search existing threads first (D01, D04).  
5. Success metric: replies describing current workaround + willingness to try a notebook — not upvotes.

---

## Venue guidelines (verified / partial)

| ID | Venue | Guideline takeaway | Fit for validation |
|----|-------|--------------------|--------------------|
| D01 | Pangeo Discourse | Civil; no spam; right category; research first | Soft technical question; note WMTS cookbook is viz-oriented (N19); analysis users may prefer COG (N20) |
| D02 | r/gis | GIS-related; no spam; show work for homework-like asks | Practitioner question OK; not “check out my lib” |
| D03 | r/Python | Help → r/learnpython; showcases need repo; allergy to AI/low-effort spam | **Avoid** cold alpha showcase now |
| D04 | GIS Stack Exchange | Concrete lasting Q; not vendor support desk | Ask about WMTS→array practice; optional self-answer later |
| D05 | OWSLib issues | Repro bugs/docs only; CoC; no competitor marketing | Docs PR / footgun repro only — **never** “use tilearray instead” |

---

## Low-effort sequence (suggested order)

| Step | Action | Effort | Expected signal |
|------|--------|--------|-----------------|
| 1 | List 5 people/orgs who already tile WCS (SoilGrids/3DEP/EA-like) from GitHub issues/tutorials; personal email/DM with notebook link | Low | Level 3 conversations |
| 2 | Post draft A on Pangeo (category: general or data access) | Low | How experts mosaic today |
| 3 | Post draft B on GIS SE if no duplicate | Low | Durable Q&A + maybe level 3 |
| 4 | Optional draft C on r/gis (not r/Python) | Low | Practitioner chatter |
| 5 | Offer office-hours / async review of one external AOI config (no feature work) | Medium | Level 4 attempt |
| 6 | Skip OWSLib marketing; optionally contribute a WCS docs clarifying example upstream (D05) | Medium | Goodwill, not adoption |

**Stop / pivot if:** replies unanimously say “GDAL WMS XML + rioxarray” or “I only need GeoTIFF in QGIS” with no interest in lazy arrays.

---

## Draft A — Pangeo Discourse (do not post yet)

**Title:** How do you pull large AOI mosaics from WCS/WMTS into xarray today?

**Body (draft):**

> I’m looking for current practice from people who regularly pull **analysis** rasters from OGC endpoints (not just basemap viz).
>
> Context: for elevation/soil-style **WCS**, I’ve hit server max width/height and ended up subsectioning + merging (similar to discussions around OWSLib + rasterio.merge). For **WMTS**, georeferencing tiles or automating bbox patches often means custom tile-index maths or GDAL WMTS/WMS XML.
>
> Questions:
> 1. What’s your default path today for AOI → xarray (or GeoTIFF then xarray)? OWSLib, GDAL drivers, HyRiver/PyGeoOGC, leafmap/tms2geotiff, something else?
> 2. When do you prefer a **lazy** mosaic vs writing a GeoTIFF checkpoint?
> 3. Any hosts where concurrency/backoff mattered more than the client API?
>
> I’m the author of an early alpha (`tilearray`) that tries one `create_array` for WCS/WMS/WMTS/XYZ → Dask/xarray with a shared fetch policy. I’m **not** pitching it as the answer — I want to know whether that shape is useful versus what you already use. Happy to share a small EA Lidar / GIBS-scale notebook if useful.
>
> (Will follow tile-provider ToS; not interested in bulk OSM scraping.)

---

## Draft B — GIS Stack Exchange (do not post yet)

**Title:** Practical ways to mosaic WMTS (or size-limited WCS) into a georeferenced array in Python?

**Body (draft):**

> I need an AOI + target resolution as a georeferenced NumPy/xarray array from services that only expose WMTS tiles or WCS with max width/height.
>
> I know:
> - OWSLib `gettile` does not attach georeferencing by itself (see related WMTS questions).
> - GDAL WMTS/WMS drivers + `gdal_translate`/`rioxarray` can open virtual mosaics.
> - For WCS, tiling below server limits + `rasterio.merge` is a common workaround.
>
> For a **repeatable Python pipeline** (not QGIS Save as…), what are people using in 2025–2026 that they would still recommend? I’m especially interested in trade-offs between (a) GDAL XML drivers, (b) download-to-GeoTIFF helpers (contextily/leafmap/tms2geotiff), and (c) custom clients.
>
> Constraints: prefer analysis-ready numerics when the service is WCS; understand WMS/WMTS are often rendered pictures.

*(Optional later self-answer may mention tilearray with limitations — only after others answer.)*

---

## Draft C — r/gis (do not post yet)

**Title:** Python folks: WCS/WMTS AOI → array — what do you actually use?

**Body (draft):**

> Curious what working geospatial Python people use when a portal only gives WMTS or a WCS that rejects large GetCoverage requests.
>
> I’ve used / seen: OWSLib + manual tiling, GDAL WMS/WMTS XML, HyRiver PyGeoOGC for WMS, leafmap `map_tiles_to_geotiff`, QGIS plugins for offline GeoTIFF.
>
> If you’ve shipped a pipeline: did you want a **GeoTIFF file** or a **lazy xarray**? Any “I wish this existed” gaps still open?
>
> (Disclosure: I maintain a small alpha aimed at that gap — mainly asking what already works.)

---

## What not to do

- Cold r/Python showcase of alpha with 0 stars.  
- Bulk OSM demos or “download the city” marketing (G01).  
- Claim WMS mosaics are numerical DEMs (P10).  
- File OWSLib issues that are really ads (D05).  
- Promise roadmap features as the validation hook — ask about **current** value.

---

## Tracking template (for later)

| Date | Venue | Link | Level 3? | Level 4? | Key quote | Follow-up |
|------|-------|------|----------|----------|-----------|-----------|
| | | | | | | |
