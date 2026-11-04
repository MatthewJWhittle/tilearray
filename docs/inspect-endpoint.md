# Inspect an unknown endpoint

Point tilearray at a WMS, WMTS, or WCS URL, read what the host advertises, then build a **validated** config for {py:func}`~tilearray.create_array`. XYZ templates have no GetCapabilities — the helper explains that and only checks the URL shape.

This is discovery and validation, not automatic reprojection or a provider catalogue.

## WCS — list coverages, then load

```python
from tilearray import compute_with_policy, create_array, inspect_endpoint
from tilearray.types import CRS

url = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)

inspection = inspect_endpoint(url)
print(inspection)  # CoverageIds, formats, CRS (secrets in params/headers redacted)

# CoverageId comes from GetCapabilities — not the URL path slug
config = inspection.build_config(
    coverage_id=(
        "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
    ),
)
# Or: print(inspection.example_snippet(coverage_id="…"))

da = create_array(
    config,
    bbox=(431900.0, 382700.0, 432700.0, 383500.0),
    bbox_crs=CRS.EPSG_27700,
)
elevation = compute_with_policy(da)
```

`build_config` refuses unknown CoverageIds. If the host lists exactly one coverage (or one library format / CRS), that value can be filled in; otherwise you must choose. Compose politeness separately when you need it (`**ea_dsp_fetch_defaults()` on a `WCSConfig.from_url` you already trust).

## WMTS — layer, matrix set, matrix

```python
from tilearray import inspect_endpoint

# Any WMTS GetCapabilities URL your host documents
inspection = inspect_endpoint("https://example.com/path/to/wmts")
print(inspection)  # layers, TileMatrixSets, matrix ids, REST templates when present

config = inspection.build_config(
    layer="blue_marble",
    tile_matrix_set="GoogleMapsCompatible",
    tile_matrix=5,
)
# create_array(config, bbox=…, bbox_crs=…) as usual
```

Pass `layer`, `tile_matrix_set`, and `tile_matrix` unless only one option exists. Wrong ids raise {py:exc}`~tilearray.ValidationError` — tilearray does not rename or guess. Inspection does **not** warp pixels: for metadata-backed WMTS the mosaic stays on the matrix SupportedCRS (your bbox `crs=` is only the query window); see {doc}`guides/wmts` and {doc}`design-principles`.

## WMS and private hosts

```python
inspection = inspect_endpoint(
    "https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms",
)
config = inspection.build_config(layers="Vegetation_Object_Model_2022")
```

Year-suffixed WMS layer names are a common footgun — confirm them in the summary before hard-coding. For keyed or private endpoints, pass the same `headers=` / `params=` you would on a config; capability requests use that auth route, and printed summaries redact secret query keys and sensitive headers.

## XYZ templates

```python
inspection = inspect_endpoint("https://tile.openstreetmap.org/{z}/{x}/{y}.png")
config = inspection.build_config(zoom=16)
```

There is nothing to discover via GetCapabilities. Check `{z}/{x}/{y}` order against the host (some use `{z}/{y}/{x}` — see {doc}`example-sources`).

Everyday use is {py:func}`~tilearray.inspect_endpoint` with a URL. Offline / fixture parsing exists as {py:func}`~tilearray.inspect_capabilities` for tests or saved GetCapabilities XML — not the main entry point.

## Related

- {doc}`quickstart` — known public hosts
- {doc}`troubleshooting` — wrong CoverageId / format / URL symptoms
- {doc}`api/index` — `inspect_endpoint` (URL) / `inspect_capabilities` (offline body) / `EndpointInspection`
