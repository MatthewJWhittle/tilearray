# Glossary

Shared vocabulary for tilearray internals and docs. Prefer these names in code comments, issues, and PRs.

## Placement

**Placement** is the step that turns a **decoded tile** plus **request geometry** into a **request-shaped array**.

- Exact shape → pass-through.
- Undersize clipped GeoTIFF (usable `ModelPixelScale` / `ModelTiepoint`) → integer offset onto the request canvas with coverage nodata pads (not soft-fail fill).
- Oversize → downsample only (never upsample / stretch).
- Mismatched resolution, CRS, rotation, or missing georef → loud `ValidationError`.

Owned by `tilearray.placement` (`fit_tile_to_request`). Decode stays pixels-from-bytes; mosaic CRS/transform attrs stay plan-time.

## MosaicPlan

**MosaicPlan** is the typed result of planning tiles for one AOI: HTTP **tile requests** plus optional native **mosaic bbox/CRS** and **grid shape**.

- Indexed fixed grids (XYZ, metadata/slippy WMTS) → `mosaic_bbox` / `grid_shape` describe the tile-union extent.
- Query-window planners (WMS/WCS) → those fields stay unset; `ArrayRequest` keeps the prepared query bbox/CRS.

Owned by `tilearray.mosaic_plan`. Services no longer publish informal `inferred_*` attributes; `ArrayRequest.plan_tile_requests` applies the plan locally.

## Entry builder (planned)

Internal shared builder for the duplicated `create_array` / `plan_array` preamble (config vs URL, CRS/output_crs, fetch policy). Name at ticket time (`_entry` / `from_public_args`). Follow-on deepening after MosaicPlan.
