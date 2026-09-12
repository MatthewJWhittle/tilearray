# tilearray

Load geospatial tiles from remote OGC-style services into lazy **xarray** / **dask** arrays.

[![Tests](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml)
[![Build and Publish](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml)

> **Alpha status.** The API and behaviour may change. Only **Web Coverage Service (WCS)** is implemented today. WMS, WMTS, and XYZ appear in type definitions and configuration but are not yet supported.

## Features

- **WCS support** — fetch GeoTIFF coverage tiles from WCS 2.0.1 endpoints
- **Lazy arrays** — `create_array` returns a Dask-backed `xarray.DataArray` you can compute when ready
- **Service configuration** — `WCSConfig` / `ServiceConfig` describe endpoints, CRS, chunking, and caching
- **Extensible services** — register custom service implementations via the service registry

## Installation

### Using uv (recommended)

```bash
uv add tilearray
```

### Using pip

```bash
pip install tilearray
```

Requires Python 3.9 or later.

## Quick start

This example uses the public [Environment Agency Lidar DTM WCS](https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs) (see [example-sources.md](example-sources.md) for the URL and coverage id).

```python
from tilearray import create_array
from tilearray.service import WCSConfig
from tilearray.types import CRS, Format

wcs_url = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)
coverage_id = "lidar-composite-digital-terrain-model-dtm-1m"

config = WCSConfig.from_url(
    wcs_url,
    coverage_id=coverage_id,
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(800, 800),
    grid_shape=(1, 1),
)

# 800 m × 800 m tile in British National Grid (EPSG:27700)
bbox = (431900.0, 382700.0, 432700.0, 383500.0)

da = create_array(
    service_url=config,
    bbox=bbox,
    crs=CRS.EPSG_27700,
)

# Lazy until you call .compute() or .load()
elevation = da.compute()
print(elevation.shape, float(elevation.mean()))
```

### Public API

| Export | Description |
|--------|-------------|
| `create_array`, `load_array` | Build lazy or eager xarray arrays from a service |
| `WCSService`, `WCSParser` | Low-level WCS client and capabilities parser |
| `WCSConfig`, `ServiceConfig` | Typed configuration for service endpoints |
| `get_service`, `register_service`, `detect_service_type` | Service registry helpers |

Import service types from `tilearray.service` (for example `from tilearray.service import WCSConfig`).

## Development

### Prerequisites

- Python 3.9+
- [uv](https://github.com/astral-sh/uv) for dependency management

### Setup

```bash
git clone https://github.com/MatthewJWhittle/tilearray.git
cd tilearray
uv sync --dev
make pre-commit
```

### Running tests

```bash
# Unit tests (default)
make test

# With coverage report
make test-cov

# Integration tests against live WCS endpoints (slow)
uv run pytest -m integration
```

See [guidelines.md](guidelines.md) for project conventions and [TEST_IMPROVEMENT_PLAN.md](TEST_IMPROVEMENT_PLAN.md) for testing notes.

### Code quality

```bash
make lint
make format
make pre-commit-run
```

## Project structure

```
tilearray/
├── src/tilearray/          # Library source
│   ├── array.py            # create_array / load_array
│   ├── service/            # WCS and service registry
│   └── types.py            # CRS, Format, bounding boxes, etc.
├── tests/
│   ├── unit/
│   └── integration/
├── notebooks/examples/
├── guidelines.md
└── example-sources.md
```

## Contributing

1. Fork the repository and create a feature branch.
2. Make your changes and run `make test && make lint`.
3. Open a pull request against `main`.

## License

This project is licensed under the Apache License 2.0 — see [LICENSE](LICENSE).
