---
file_format: mystnb
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

# Fetch presets (offline)

This vignette only constructs configs and inspects fetch-policy knobs. It does **not** call remote hosts.

Presets exist because public hosts want **different** polite behaviour — not as a product catalogue of every coverage id.

```{code-cell} python
from tilearray import XYZConfig
from tilearray.service import WCSConfig
from tilearray.types import CRS, Format

osm = XYZConfig.for_openstreetmap(zoom=16)
print("OSM template:", osm.base_url)
print(
    "OSM policy:",
    {
        "max_concurrent": osm.max_concurrent_requests,
        "adaptive": osm.adaptive_concurrency,
        "rate_limit_per_second": osm.rate_limit_per_second,
        "user_agent": osm.headers.get("User-Agent"),
    },
)
```

```{code-cell} python
ea = WCSConfig.for_ea_dsp(
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs",
    coverage_id=(
        "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
    ),
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(128, 128),
)
print(
    "EA DSP policy:",
    {
        "max_concurrent": ea.max_concurrent_requests,
        "initial": ea.initial_concurrent_requests,
        "min": ea.min_concurrent_requests,
        "adaptive": ea.adaptive_concurrency,
        "circuit_breaker": ea.forbidden_circuit_breaker,
        "rate_limit_per_second": ea.rate_limit_per_second,
    },
)
```

## What to remember

- **OSM** — fixed low concurrency and a rate limit; identifiable User-Agent required by OSMF tile policy.
- **EA DSP** — AIMD concurrency under a hard ceiling (default max **10**), plus a pressure circuit breaker for gateway storms.
- For custom endpoints use `from_url` and tune `FetchPolicy` / config fields yourself.
- Multi-tile lazy mosaics: prefer `compute_with_policy` so Dask workers match the fetch ceiling.

See also [`example-sources.md`](https://github.com/MatthewJWhittle/tilearray/blob/main/example-sources.md) for peer stress targets.
