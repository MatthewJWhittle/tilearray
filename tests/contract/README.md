# Contract tests (recorded HTTP)

Contract tests replay **VCR cassettes** (YAML files) instead of calling live
services. They lock in WCS / WMTS request/response behaviour for public hosts
while keeping default CI network-free.

## What green means (testing layers)

| Layer | Where | PR? | Role |
|-------|-------|-----|------|
| Unit | `tests/unit/` | Yes | Library logic under mocks |
| Contract (VCR) | `tests/contract/` | Yes | Recorded caps / plan locks |
| Live / soak | `tests/integration/` (`@net` / `@slow`) | Nightly only | Real hosts; discovery + plot-ready smoke |
| Docs journey | `scripts/docs_journey_smoke.py` | Manual / local | Opaque inspect + palette decode without Jupyter |

**PR green ≠ stage-gate notebooks pass.** Coverage ≥ 85% is a floor, not the definition of product readiness. Unit tests assert on library behaviour, not docs prose.

### Adding a URL-shape or inspect case

1. Prefer a taxonomy case in `tests/unit/` (opaque path, `SERVICE=` query, token path, XYZ, unknowable → message) over a host-named test.
2. For inspect journeys, feed **real-shaped caps XML** under an opaque `base_url` via respx — typed `WMTSConfig.from_url` live smoke does **not** cover discovery.
3. Record a VCR cassette only when locking live HTTP shapes; share one GetCapabilities cassette across methods when bodies are equivalent for asserts.

## Layout

```
tests/contract/
├── README.md
├── wcs/
│   ├── test_wcs_contract.py
│   └── cassettes/          # one YAML per test method
│       ├── TestEALidarWCSContract.test_get_capabilities.yaml
│       ├── TestEALidarWCSContract.test_describe_coverage.yaml
│       ├── TestEALidarWCSContract.test_get_coverage.yaml
│       ├── TestUSGS3DEPWCSContract.test_describe_coverage_axis_labels.yaml
│       └── TestUSGS3DEPWCSContract.test_get_coverage_multipart_geotiff.yaml
└── wmts/
    ├── test_wmts_contract.py
    └── cassettes/
        └── TestKartverketWMTSContract.get_capabilities_utm33n_matrix.yaml
            # shared by get_capabilities + plan_tiles via vcr_cassette_name
```

Keep cassettes small (CI enforces a per-file size budget via
`scripts/check_cassettes.py`). Prefer tiny request windows when recording;
multipart GeoTIFF bodies bloat YAML quickly. WMTS Kartverket contract methods
share one GetCapabilities cassette (layer-order diffs are irrelevant to
asserts); planning stays offline against that recorded XML / mocked PNG.

## Running

```bash
# Default PR/CI selection (unit + contract, no network)
uv run pytest -m "not slow and not net"

# Contract tests only
uv run pytest tests/contract -m contract
```

Live integration tests live under `tests/integration/` and are marked
`@pytest.mark.slow` and `@pytest.mark.net`.

## Re-recording cassettes

Re-record when the upstream service changes in a way that should update the
locked contract (new coverage IDs, schema changes, etc.).

1. Delete the cassette(s) to refresh, or pass `--vcr-record=all` to overwrite.
2. Run with network access and **once** recording mode:

```bash
uv run pytest tests/contract/wcs/test_wcs_contract.py \
  --vcr-record=once -m contract
```

3. Review the diff in `tests/contract/wcs/cassettes/` — confirm no secrets
   appear (Authorization, cookies, API keys are filtered in `conftest.py`).
4. Refresh integrity stamps (required — CI fails when URI/body hashes drift):

```bash
uv run --no-project --with pyyaml python scripts/check_cassettes.py --stamp
```

5. Commit the updated YAML + stamps alongside any assertion changes.
   **Do not hand-edit request URIs** while keeping an old response body
   (`scripts/check_cassettes.py` integrity stamps catch that; see #177).

To force a live run without playback (e.g. debugging), use `--disable-vcr`.

## Source endpoints

- **WCS**: EA Lidar Composite DTM 1 m — `https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`
- **WMTS**: Kartverket topo / utm33n — `https://cache.kartverket.no/v1/service`
- Documented in [`docs/example-sources.md`](../../docs/example-sources.md) (stub at repo root points at the hosted page).
