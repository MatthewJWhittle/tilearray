# Tile-fetch / array-build engine review

**Scope:** `main` @ `baf9fcf` (includes merged PR #9). Original review-only deliverable from PR #10; **thin-path implementation landed in PR for this branch.**

**Matt’s target UX (for gap analysis):** define a request → controlled background tile acquisition → progress → assemble raster; keep a lazy path; per-server rate limits; optimise engine first; prefer existing libs; keep install small.

---

## Implementation status (thin path)

| Recommendation | Status |
|----------------|--------|
| `TileFetcher` centralising HTTP on shared `httpx.Client` | **Implemented** (`src/tilearray/fetch.py`) |
| Bounded concurrency (semaphore + pool limits, default 3) | **Implemented** |
| Retries via `tenacity` (backoff + jitter, 429/5xx, `Retry-After`) | **Implemented** |
| Pluggable per-host rate limiter via `ServiceConfig` | **Implemented** (`rate_limiter` hook + default `PerHostRateLimiter` at 2 req/s) |
| Optional `on_progress(done, total, …)` callback | **Implemented** on `create_array` / `load_array` |
| Wire fetcher into `_load_tile_array` / `fetch_tile` | **Implemented** |
| Move `respx` to dev dependencies | **Implemented** |
| Fake-server unit tests (429, 503, concurrency, rate limit, progress) | **Implemented** (`tests/unit/test_fetch.py`) |

### Deferred (explicitly out of scope)

- Full asyncio rewrite / job queue
- ETag/LRU cache redesign (existing SHA-256 disk cache retained)
- WMS/WMTS/STAC integration
- Migrating WCS metadata `requests.Session` to httpx (still uses `requests`)
- Dask `num_workers` documentation as primary concurrency knob (semaphore is now the supported in-process limit)

---

## 1. Current flow (post-implementation)

### Request → array (sync planning, lazy execution)

- **`create_array`** (`array.py`): normalises bbox/CRS → builds `ArrayRequest` → instantiates service → **`plan_tile_requests`** → **`_organize_tiles`** → one **`dask.delayed(_load_tile_array)`** per tile → **`da.block`** → `xr.DataArray`.
- **`FetchPolicy`** resolved from `ServiceConfig` (or defaults) and passed into each delayed tile task.
- **`FetchProgress`** tracks completed tiles; optional **`on_progress`** callback; failures logged and surfaced via **`warnings.warn`** when `compute=True`.

### `fetch_tile` → decode

- **`TileFetcher.fetch`** (`fetch.py`): shared **`httpx.Client`**, **`threading.Semaphore(max_concurrent)`**, optional **`HostRateLimiter`**, **`tenacity`** retries for 429/502/503/504 and transport errors.
- **`_load_tile_array`**: disk cache check → `fetch_tile(policy=…)` → decoder → failed tiles remain **NaN** (unchanged default).
- **Cache:** unchanged SHA-256 file store.

### Concurrency model

- Dask still schedules tile tasks; **`TileFetcher`** enforces a global in-process cap on concurrent HTTP GETs (default **3**, conservative rate limit **2 req/s/host**).
- Tune via `ServiceConfig.max_concurrent_requests`, `rate_limit_per_second`, or `create_array(..., max_concurrent_requests=N)`.

---

## 2. Original gaps (pre-implementation)

| Goal | Before |
|------|--------|
| Controlled acquisition | Unbounded Dask threaded scheduler |
| Progress updates | None |
| Per-server rate limits | None |
| Retry/resilience | Immediate re-try loop in `requests.get` |
| Centralised HTTP | Per-call `requests.get` |

---

## 3. Reuse options (reference)

See original review table in PR #10 history. **Chosen stack:** `httpx` + `tenacity` + in-process semaphore + lightweight `PerHostRateLimiter` (no `pyrate-limiter` dep).

---

## 4. Risks (still relevant)

- **WCS vs XYZ:** tune `max_concurrent_requests` / `rate_limit_per_second` per host or service type.
- **EA rate behaviour:** defaults remain conservative; integration tests still avoid parallel bursts.
- **Silent NaN on failure:** use `on_progress` or inspect logs/warnings for failed tile URLs.
- **Lazy compute later:** failure summary `warnings.warn` only runs when `create_array(..., compute=True)`; calling `.compute()` on a returned lazy array does not re-run the summary hook.
