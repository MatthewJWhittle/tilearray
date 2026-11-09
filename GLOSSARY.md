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

## Entry builder

**Entry builder** is the shared create/plan preamble: public argument bag → validated request, service, fetch policy, and planned tiles.

Owned by `tilearray._entry` (`from_public_args` → `ArrayEntry`). `create_array` and `plan_array` only diverge after that into mosaic assembly vs plan summary. Planning helpers stay in `_planning`; this module composes them.
