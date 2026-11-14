# Polite fetch and scaling

Large AOIs mean dozens or thousands of tile requests. Without a shared concurrency ceiling — and without knowing how soft-fail and process-local gates behave — a laptop-sized demo turns into 403s, 429s, or a flooded public host when you widen the window or add workers.

This how-to covers host ceilings (AIMD / rate limits), soft-fail phases, the ``**service_options`` keyword bag on `create_array`, and multi-process limits. Assumes a working `create_array` → `compute_mosaic` path ({doc}`quickstart`). Pipeline overview: {doc}`how-it-works`. Symptom tables: {doc}`troubleshooting`.

## Host ceilings and AIMD

Fetching uses **synchronous HTTP** with a process-local concurrency gate:

- Dask’s **thread** pool runs tile tasks; the gate caps in-flight HTTP (not asyncio).
- Fixed ceiling + optional rate limit when adaptive mode is off (OSM preset).
- **AIMD** when enabled: ramp under `max_concurrent`, back off on pressure (403/408/429/5xx and known OGC transient responses).
- Optional **circuit breaker** (EA DSP preset): a burst of pressure freezes concurrency at the floor for a cooldown.
- Retries with `Retry-After` support; exhausted **pressure** failures (429/5xx/timeout/transport) get one deferred retry pass after siblings finish (AIMD floor; park budget ~10%) before hard `NetworkError`. Permanent client errors still fail fast.
- Timeouts: shorter **connect** vs idle **read** via `httpx.Timeout`, plus a separate wall-clock **tile deadline** per attempt (`fetch_timeout` / `fetch_connect_timeout` / `fetch_tile_deadline`). Idle-read alone is not a whole-response deadline.

Host **presets** (`osm_fetch_defaults`, `ea_dsp_fetch_defaults`) set polite defaults — compose onto `from_url`. Stay at or below preset ceilings on multi-tile cold mosaics. AIMD only helps if Dask can schedule enough tile loads — use `compute_mosaic`.

### Concurrency names

**Host ceiling ≠ call politeness ≠ Dask threads.** Do not shrink `FetchPolicy.max_concurrent` to “be polite” — that lowers the shared process gate for sibling sessions.

| Role | Name | Where |
|------|------|-------|
| Host ceiling (process gate) | `max_concurrent_requests` → `FetchPolicy.max_concurrent` | Preset / service config / raised override on `create_array` |
| Call-local politeness | `call_max_concurrent` (or a *lower* `max_concurrent_requests=` on one call) | That mosaic only; siblings keep the host ceiling |
| Dask thread workers | `compute_mosaic(..., num_workers=)` | Sizes the thread pool for compute; does **not** change HTTP in-flight |

## Soft-fail phases

Opt-in `on_tile_error` / `soft_fail=True` can soft-fill exhausted tile-local failures after retries (and any deferred pressure pass) without aborting siblings. Soft-fail is plan-time only — the default remains hard `NetworkError` + sibling abort after recovery attempts.

| Phase | When | Soft-fail? |
|-------|------|------------|
| Pre-graph band probe | `create_array` discovers band count for `preserve` formats (GeoTIFF / PNG / JPEG) | **Hard** — probe ignores soft-fail; pass `band_policy="first_band"` to skip the probe |
| Post-graph tile load | Mosaic tiles after the Dask graph exists | Soft-fill exhausted tile-local 404 / 5xx / transport; record `attrs["tilearray_tile_errors"]` |
| Auth / rate-limit | HTTP 401 or exhausted 429 (after deferred pass) | **Hard abort** (siblings stop) |

Soft-fail knobs (`on_tile_error`, `soft_fail`, `soft_fill_value`, `soft_fail_max_fraction`) are **named** parameters on `create_array` / `load_array`, not keys in the ``**service_options`` bag.

## ``**service_options`` name map

``create_array(..., **service_options)`` is one bag. Keys fall into three families — know which family you are editing:

| Family | Keys (illustrative) | What consumes them |
|--------|---------------------|--------------------|
| **Fetch** | `max_concurrent_requests`, `max_connections`, `fetch_retries`, `fetch_timeout`, `fetch_connect_timeout`, `fetch_tile_deadline`, `rate_limit_per_second`, `rate_limiter`, AIMD / circuit-breaker fields (`adaptive_concurrency`, `initial_concurrent_requests`, …) | Popped into `FetchPolicy` before planning |
| **Planner** | `resolution`, `origin` | Snap / grid planning; leftovers join tile options |
| **Identity / protocol** | `headers`, `params`, coverage / layer / style / TMS / zoom… | Leftover options → service / tile request |

Named parameters (`on_progress`, `cache_dir`, soft-fail, `band_policy`, …) are not bag keys. API surface: {doc}`api/index`.

## Scaling without overwhelming the host

The fetch gate is **process-local**. Threads in one Python process share it; separate OS processes and Dask distributed workers do **not**. Using more workers the wrong way multiplies in-flight HTTP by worker count even when each worker looks polite.

| Scale shape | What the gate does | Do this |
|-------------|--------------------|---------|
| **One process, Dask threads** | Shared ceiling holds for sibling mosaics | Prefer {py:func}`~tilearray.compute_mosaic` (always uses `scheduler="threads"`). Keep preset host ceilings; lower per-call with `max_concurrent_requests=` / `call_max_concurrent`. |
| **Several mosaics, same process** | Aggregate in-flight ≤ host ceiling | Same configs / presets; do not shrink `FetchPolicy.max_concurrent` for one polite call. |
| **Multi-process or distributed workers** | Each process has its own gate — **no global ceiling** | Do **not** advertise a cluster-wide host limit. Either stay on threads, or set each worker’s ceiling to about `floor(preset_ceiling / N_workers)` and treat that as a conservative approach, not a library guarantee. |

**What not to do**

- Do not use `da.compute(scheduler="processes")` or a Dask distributed cluster expecting the AIMD / semaphore gate to protect the host.
- Do not raise `max_concurrent_requests` past the preset to “use the bigger machine”.
- Do not launch N independent Python jobs against the same host each at the full preset ceiling.
- Do not expect tilearray to coordinate politeness across machines.

`Retry-After`, AIMD pressure backoff, circuit breakers, and hard-fail / soft-fail abort still apply **inside** each process. Bigger machines help when the bottleneck is CPU/RAM for decode and assemble under the preset host ceiling — not by multiplying concurrent HTTP requests.

Many noncontiguous sample boxes: {doc}`multi-aoi-ml-patches`.
