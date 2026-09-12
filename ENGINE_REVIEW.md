# Tile-fetch / array-build engine review

**Scope:** `main` @ `baf9fcf` (includes merged PR #9). Review only — no implementation.

**Matt’s target UX (for gap analysis):** define a request → controlled background tile acquisition → progress → assemble raster; keep a lazy path; per-server rate limits; optimise engine first; prefer existing libs; keep install small.

---

## 1. Current flow

### Request → array (sync planning, lazy execution)

- **`create_array`** (`array.py`): normalises bbox/CRS → builds `ArrayRequest` → instantiates service (`get_service` / `ServiceConfig.build_service`) → **`plan_tile_requests`** → **`_organize_tiles`** (sort by `-y`, `x`) → one **`dask.delayed(_load_tile_array)`** per tile → **`da.block`** → `xr.DataArray` with linspace `x`/`y` coords.
- **`load_array`**: `create_array(..., compute=False)` then `.compute()` (eager by default).
- **`compute=True`** on `create_array` materialises immediately; otherwise nothing is fetched until `.compute()` / `.load()`.

### `service.generate_tile_requests`

- **`BaseService.generate_tile_requests`**: `plan_tiles(bbox, chunk_size, **options)` → `build_tile_request` per `TileGeometry`.
- **WCS** (`wcs.py`): resolution-driven scan *or* fixed `grid_shape` subdivision; builds GetCoverage query params (`subset`, `coverageId`, `width`/`height`). Capabilities/describe use a persistent **`requests.Session`** on the service object.
- **XYZ** (`xyz.py`): maps bbox → tile index range at fixed `zoom`; sets **`inferred_grid_shape`**. `ArrayRequest.plan_tile_requests` upgrades `grid_shape` from `(1,1)` when inferred grid &gt; 1 tile.
- **`create_tile_grid`** (`tiles.py`): pure origin-aligned grid math — **not wired** into `plan_tiles` / `create_array` yet (parallel tiling strategy).

### `fetch_tile` → decode

- **`fetch_tile`** (`tiles.py`): **sync `requests.get`**, per-call (no shared session/pool from tilearray). `params` optional (XYZ URLs can be fully formed).
- **Retries:** `request.retries + 1` attempts (default 4); **immediate re-try**, no backoff; retries non-200 and network errors alike; no `Retry-After` / 429 awareness.
- **`_load_tile_array`**: disk cache check → `fetch_tile` → registered decoder (GeoTIFF / PNG-JPEG) → optional downsample → failed tiles become **NaN** arrays (no raise).
- **Cache:** SHA-256 file key (`url`, `params`, `format`, `bbox`, `width`, `height`) → `{hash}.tile` under `cache_dir`. Write-through on success. No ETag/TTL/LRU.

### Concurrency model today

- **Lazy path = Dask graph.** Parallelism only appears at **`.compute()`**, governed by the **Dask scheduler** (default: threaded, **unbounded** up to task count).
- Each task runs **blocking HTTP** in a worker thread. No tilearray-level `max_workers`, semaphore, or rate limit.
- Live EA multi-tile test comment: *“sequential requests for stability”* — implicit acknowledgment that unconstrained parallelism is risky.

---

## 2. Gaps vs goal

| Goal | Today |
|------|--------|
| Controlled async acquisition | Only via Dask `.compute()`; no explicit worker pool or fetch budget |
| Progress updates | None (no callback, no tqdm hook, no structured events) |
| Per-server rate limits | None; 429 likely fails tile → NaN after fixed retries |
| True async HTTP | Sync `requests` in threads; `httpx` in deps but **unused** in `src/` |
| Cancellation | No cooperative cancel; Dask task cancel is best-effort and not exposed |
| Backpressure | All N tiles become delayed tasks immediately; scheduler may flood server |
| Retry/resilience (per `guidelines.md`) | No exponential backoff, no jitter, no honour `Retry-After`, no retryable-code filter |
| “Define request, then background fetch” | Partially: graph is built eagerly, bytes fetched lazily — no separate acquisition API / queue |
| Cache sophistication | File store only; no conditional GET |

---

## 3. Reuse options (brief; deps weight)

| Option | Fit | Weight / notes |
|--------|-----|----------------|
| **`httpx` + `ThreadPoolExecutor` + semaphore** | Drop-in sync fetch with shared client, pool limits, HTTP/2 optional | Already declared; ~small (+ `httpcore`). **Lowest-risk path with Dask.** |
| **`httpx` / `aiohttp` + asyncio + semaphore** | True async I/O | `aiohttp` heavier; **async + Dask** needs careful bridging (run async loop in executor or custom scheduler). |
| **`tenacity`** | Retry/backoff/jitter, `Retry-After`, retry predicates | Tiny (~1 dep). Composable wrapper around fetch. |
| **`backoff`** | Simpler retry decorator | Tiny; less expressive than tenacity. |
| **`pyrate-limiter`** | Token-bucket / fixed window, sync + async | Small; good for “N req/s per host” configs. |
| **`aiolimiter`** | Async semaphore-style rate limit | Tiny; async-only. |
| **Dask delayed/futures (current)** | Lazy composition, familiar to xarray users | Already core dep (large). Keep as assembly layer. |
| **`stackstac` / `odc-stac` / `pystac-client`** | STAC → xarray/Dask mosaics | **Heavy** (`rasterio`/GDAL stack). Wrong abstraction for arbitrary WCS GetCoverage / XYZ templates. |
| **`rasterio.session`** | GDAL VSI / signed URLs | Pulls **GDAL**; overkill for raw byte fetch + existing decoders. |
| **`urllib3.util.Retry` + `requests.Session`** | Retry on adapter | Already have `requests`; lighter than new stack but weaker than tenacity for `Retry-After`. |
| **`tqdm.dask`** | Progress during `.compute()` | Tiny if tqdm optional extra; no per-tile failure detail. |

**Also worth noting:** `respx` is a **runtime** dep today but only used in tests — should move to dev to shrink install.

---

## 4. Recommended thin path

Smallest change set that preserves lazy Dask composition:

1. **Introduce a `TileFetcher` protocol** (sync): `fetch(TileRequest) -> TileResponse`. Default impl wraps current logic.
2. **Centralise HTTP in one place** — migrate `fetch_tile` to **`httpx.Client`** (shared, context-managed or module singleton keyed by `(base_url, headers)`):
   - **`limits=httpx.Limits(max_connections=N, max_keepalive=…)`**
   - **`threading.Semaphore(max_inflight)`** around GET (or Dask **`scheduler="threads"` + `num_workers=N`** documented as the supported knob).
3. **Retry policy via `tenacity`** (or `urllib3.Retry` if avoiding new dep): retry 429/502/503/504 + network errors; exponential backoff + jitter; read **`Retry-After`** header.
4. **Pluggable rate limiter** — small interface, default no-op; ship one **`pyrate-limiter`** (sync) adapter keyed by `urlparse(netloc)`; allow per-`ServiceConfig` override (EA vs OSM vs generic).
5. **Progress** — optional `on_progress(done, total, request, response)` callback invoked from `_load_tile_array` (or fetcher wrapper); optional `[progress]` extra with `tqdm`.
6. **Keep lazy path:** unchanged public API — `create_array(..., compute=False)` + `.compute()`; pass fetcher/rate-limit/progress via `ServiceConfig` or new `FetchPolicy` kwarg.

### Explicitly do **not** build (yet)

- Full async rewrite / custom event loop
- Job queue service, worker processes, or download manager UI
- ETag/LRU cache subsystem (file cache is enough for v1 engine)
- STAC/rasterio/stackstac integration for tile HTTP
- Separate “background thread pool” API distinct from Dask compute
- WMS/WMTS until protocol support lands

---

## 5. Risks

- **WCS vs XYZ:** WCS tiles are fewer, larger, query-param heavy; XYZ is many small PNGs. One global concurrency limit may starve WCS or hammer XYZ — **rate limits should be per-host and optionally per-service-type**.
- **EA rate behaviour:** Public EA WCS has no documented quota in-repo; integration tests already avoid parallel bursts. Engine defaults should be **conservative** (e.g. 2–4 inflight, 1–2 req/s) with opt-in tuning.
- **Thread vs async with Dask:** Dask’s threaded scheduler + sync HTTP is the pragmatic fit. Pure asyncio requires either (a) running fetches outside Dask and writing into a numpy buffer, or (b) nested event loops — both add complexity for marginal gain at current scale.
- **Silent NaN on failure:** Good for mosaic continuity, bad for debugging — progress/error callback should surface failed tile URLs without changing default array behaviour.
- **Session split:** WCS service holds `requests.Session` for metadata; tile fetch uses bare `requests.get` — **connection reuse and rate limits won’t apply uniformly** until fetch is centralised.
- **Grid inference edge cases:** XYZ auto `grid_shape` only triggers when starting from `(1,1)`; explicit wrong `grid_shape` still fails in `_organize_tiles`.

---

## Suggested next milestone (implementation, not this PR)

1. `TileFetcher` + httpx + tenacity + optional pyrate-limiter  
2. Fake-server tests (429, 503, `Retry-After`) per `guidelines.md`  
3. Document `dask.config.set({"num_workers": N})` as supported concurrency knob  
4. Move `respx` → dev deps; drop duplicate `requests` once httpx covers WCS session paths
