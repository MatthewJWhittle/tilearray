# Contract tests (recorded HTTP)

Contract tests replay **VCR cassettes** (YAML files) instead of calling live
services. They lock in WCS request/response behaviour for the public EA Lidar
DTM endpoint while keeping default CI network-free.

## Layout

```
tests/contract/
├── README.md
└── wcs/
    ├── test_wcs_contract.py
    └── cassettes/          # one YAML per test method
        ├── TestEALidarWCSContract.test_get_capabilities.yaml
        ├── TestEALidarWCSContract.test_describe_coverage.yaml
        └── TestEALidarWCSContract.test_get_coverage.yaml
```

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
4. Commit the updated YAML alongside any assertion changes.

To force a live run without playback (e.g. debugging), use `--disable-vcr`.

## Source endpoint

- **Service**: EA Lidar Composite DTM 1 m WCS
- **URL**: `https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`
- Documented in `example-sources.md` at the repo root.
