# Engineering guidelines — tilearray

Product ethos, spatial contract, and scope live in **[docs/design-principles.md](docs/design-principles.md)**. This file is for people changing the repository: layout, quality tools, tests, and the API design checklist used in PR review.

Coding agents: start at **[AGENTS.md](AGENTS.md)**.

---

## 1. Layout

Use the `src/` layout. Public surface is whatever `__init__.__all__` exports.

```text
tilearray/
├─ src/tilearray/          # package (array, fetch, decode, service/, …)
├─ tests/
│  ├─ unit/
│  ├─ contract/            # VCR / recorded HTTP
│  ├─ integration/         # @net / @slow / @soak
│  └─ data/
├─ docs/                   # Sphinx (MyST) — not MkDocs
├─ scripts/                # docs artifacts, soaks, …
├─ notebooks/
├─ pyproject.toml
├─ Makefile
├─ AGENTS.md
├─ CONTRIBUTING.md
├─ CHANGELOG.md
└─ README.md
```

- Prefer **composition** over inheritance; use `Protocol`s where interfaces help.
- Avoid global state; pass configuration explicitly (dataclasses / pydantic models).
- Separate planning, HTTP fetch, decode, and mosaic assembly; don’t overload one kwarg with three jobs.

---

## 2. Build & packaging

- **`pyproject.toml`** (Hatchling) + **uv** for envs/locks.
- **Semantic Versioning**; version from VCS tags (`hatch-vcs`).
- Supported Python: **3.9–3.12** (`requires-python = ">=3.9"`).
- Keep runtime deps minimal; optional stacks via extras. Ship `py.typed`.
- Prefer fewer meaningful PyPI cuts; land work on `main` continuously.

---

## 3. Code quality

- **Ruff** for format + lint; **mypy** on `src/tilearray`; **pytest** for tests.
- Unit tests stay fast; mark network / long work `@pytest.mark.net` / `@pytest.mark.slow` (and `@soak` where applicable).
- Pre-commit: Ruff + related hooks (`make pre-commit`).
- Public signatures typed; don’t weaken types to ship.

```bash
make test
make lint
make docs          # Sphinx HTML under docs/_build/html/
```

---

## 4. Public API

- Explicit `__all__` in `src/tilearray/__init__.py` is the contract.
- Prefer configs + `create_array` / `load_array` / `compute_with_policy`; do not re-export parsers/services users don’t need.
- One primary compute habit: **`compute_with_policy`** (not competing README folklore).
- Host politeness: `tilearray.fetch_presets` composed onto `*Config.from_url` — **no** new `for_*` host methods on protocol configs.
- Plan-time bind: fetch policy, `on_tile_error`, custom decoders are arguments to `create_array` before the Dask graph is built.

---

## 5. Errors & logging

- Raise the `TileArrayError` hierarchy (`ValidationError`, `NetworkError`, `ParseError`, `ConfigurationError`, `ServiceError`).
- Wrap third-party failures at the public boundary; don’t raise bare `Exception`.
- Use `logging.getLogger(__name__)`; never `print` for library diagnostics.
- Prefer actionable messages (what failed, what to check) over opaque status dumps.

---

## 6. Documentation

- User docs are **Sphinx** + MyST (`docs/`, `make docs`). Do not assume MkDocs.
- Docstrings: Google or NumPy style (Napoleon).
- Usage guides should use real public hosts via notebooks / `make docs-artifacts`; the HTML build itself stays offline.
- User-visible behaviour changes → update how-it-works / troubleshooting / API / quickstart as needed, plus **CHANGELOG** `[Unreleased]`.
- Label **target design** vs **current alpha** honestly (especially CRS roles — see design principles).

---

## 7. CI

- GitHub Actions: lint, type-check, tests, docs, build on PRs.
- Default pytest: **no network** (`not slow and not net`).
- Test matrix: Python **3.9–3.12**.
- Tag `v*` publishes to PyPI (trusted publishing / attestations as configured).

---

## 8. Config, secrets, data

- No secrets in the repository. Compose credentials via config `headers` / `params` (plan-time); prefer params over secrets in URL templates.
- Tiny fixtures under `tests/data`; redacted cassettes for contract tests.

---

## 9. API design checklist (PR review)

Use with [docs/design-principles.md](docs/design-principles.md). Reject or redesign when a box fails without an explicit, documented reason.

- [ ] **Intent-based names** — the name predicts behaviour; one meaning per term across configs, errors, and docs.
- [ ] **Predictable defaults** — safe/loud/polite path unchanged unless the PR is *about* changing it (CHANGELOG + docs in the same PR).
- [ ] **Explicit lossy transforms** — no silent local warp, resample, band drop, or falsely labelled CRS; server-side CRS change is opt-in and reflected in metadata.
- [ ] **Accurate metadata** — returned CRS, transform, extent, dtype/bands/nodata match what was assembled.
- [ ] **Useful errors** — typed hierarchy; actionable text; no silent success with partial/wrong coverage.
- [ ] **Dependencies** — GDAL-free default path; no drive-by heavy stack.
- [ ] **Tests as contract** — offline unit/contract coverage of the dangerous case (not only the happy path).
- [ ] **Service politeness** — host ceiling preserved; call-local limits don’t redefine the shared gate; presets compose.
- [ ] **Access vs processing** — change stays inside fetch/assemble boundaries (or is clearly out of scope).
- [ ] **Surface area** — new public symbol justified; `__all__` + autosummary updated; no dual habit for an existing job.

Awkward patterns are **mistakes to fix**, not intentional design, unless evidence shows otherwise.

---

## 10. Testing policy

> Enforceable rules for a library that fetches tiles from web services. Keep CI fast, deterministic, and provider-agnostic.

### Goals

- Validate **logic, protocols, and resilience** without live services by default.
- Default suite: **no network**, finishes quickly on CI Linux, Python 3.9–3.12.
- Prove correctness at layers: **unit → contract (recorded) → live (opt-in)**.

### Taxonomy

1. **Unit (fast, pure):** no network — tiling math, URL building, retries/backoff, CRS handling, decode, mosaic attrs.
2. **Contract (recorded HTTP):** VCR cassettes; secrets redacted.
3. **Live integration (opt-in):** real endpoints; `@net` / `@slow` / `@soak`; nightly or maintainer only.
4. **Property-based:** hypothesis for invariants (bounds, cache keys, planner edges).
5. **Performance/smoke:** import-time and mocked throughput with timeouts where useful.

### Tooling

- **pytest** (+ xdist/timeout as configured); **hypothesis**; **httpx** + **respx**; **vcrpy** / pytest-vcr; **coverage.py**.

### Markers & CI

- Default: `-m "not slow and not net"` (project default).
- PR CI: unit + contract; no live fetches.
- Nightly: add `@net` / soak under presets; do not raise concurrency past preset ceilings.

### Mocking

- Mock network I/O, clocks, and randomness in unit tests.
- Do **not** mock away the library’s own retry/backoff/fetch-gate logic — exercise it against fakes.
- Prefer testing public behaviour over private implementation details.

### Behaviours worth asserting

- URL templating and credential placement (`headers`/`params`, not secrets in committed URLs).
- Tiling / planning correctness (no silent drop of leftover coverage; no host-illegal sub-pixel requests).
- HTTP: timeouts; retries on retryable codes; honour `Retry-After` where applicable.
- Mosaic attrs: CRS, north-up Affine transform, pixel-centre coords, band/nodata policy.
- Concurrency: shared host ceiling vs call-local caps; no FD leaks; bounded parallelism.
- Content: dtype/shape; elevation nodata → NaN; imagery band honesty.

### Bad practices

- Catching exceptions in tests to hide failures.
- Inline imports that break collection.
- Changing tests to fit broken code.
- Network or filesystem side effects in unit tests.
- Assertions without diagnostic context.
- Hidden shared state between tests.

### Checklist

- [ ] Unit coverage of planner/fetch/decode/mosaic logic with **no network**.
- [ ] Contract tests recorded; secrets redacted.
- [ ] Live tests marked and excluded from PRs.
- [ ] Fixtures reusable; tests parallel-safe.
- [ ] No bad practices from the list above.

**Policy:** do not weaken this without maintainer approval. Product “will / will not” rules remain in `docs/design-principles.md`.
