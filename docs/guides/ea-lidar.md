# EA Lidar WCS — elevation + analysis

Fetch a small British National Grid window from the Environment Agency Lidar composite DTM (WCS), plot elevation, and summarise the distribution.

Uses `WCSConfig.for_ea_dsp` (AIMD under a hard ceiling, pressure circuit breaker). Keep mosaics modest in docs regenerations — county-scale cold starts can trip gateway limits if the ceiling is raised.

## Code

```python
from tilearray import compute_with_policy, create_array
from tilearray.service import WCSConfig
from tilearray.types import CRS, Format

wcs_url = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)
coverage_id = (
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
)

config = WCSConfig.for_ea_dsp(
    wcs_url,
    coverage_id=coverage_id,
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(64, 64),
    resolution=(1.0, 1.0),
)

# ~256 m OSGB window near the documented EA bench location
bbox = (431900.0, 382700.0, 432156.0, 382956.0)

da = create_array(config, bbox, CRS.EPSG_27700)
elevation = compute_with_policy(da)  # match Dask workers to fetch ceiling
print(elevation.shape, float(elevation.mean()))
```

`create_array` stays lazy until compute. For multi-tile mosaics, prefer {py:func}`tilearray.compute_with_policy` over plain `.compute()` so Dask thread count tracks the AIMD fetch ceiling.

## Result

```{image} ../_static/guides/ea-lidar-elevation.png
:alt: EA Lidar elevation map and histogram for a small OSGB window
:width: 720px
```

From a recent generator run (values will drift slightly if you regenerate):

| Stat | Approx. |
|------|---------|
| shape | `(256, 256)` |
| mean elevation | ~176 m |
| range | see histogram panel |
| elapsed | a few seconds on a healthy network |

## Try it yourself

```bash
uv run --extra docs python scripts/generate_docs_artifacts.py --only ea
```

More public endpoints: [`example-sources.md`](https://github.com/MatthewJWhittle/tilearray/blob/main/example-sources.md) in the repository.
