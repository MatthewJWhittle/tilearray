# Tilearray

Domain language for fetching remote tiled geospatial data into georeferenced arrays. This context is **data access** (design, plan, fetch, decode, assemble) — not GIS processing or client-side warp.

## Spatial intent

**Area of interest (AOI)**:
The geographic window the caller wants data for.
_Avoid_: treating the AOI as identical to the mosaic extent; calling the AOI “the mosaic bbox”

**Mosaic extent**:
The geographic footprint of the assembled mosaic. On fixed tile grids it may be a tile-aligned union that is larger than, or snapped relative to, the AOI.
_Avoid_: mosaic bbox as a synonym for AOI; planned extent when meaning the caller’s original window

**Query CRS**:
The coordinate reference system in which the AOI is expressed.
_Avoid_: `crs` alone for this role; conflating query CRS with the mosaic CRS

**Service CRS**:
The CRS used when asking the provider for data (the request sent to the host).
_Avoid_: assuming it always equals the query CRS; calling it “output CRS”

**Mosaic CRS**:
The actual CRS of the returned pixel grid. Publicly surface it under a clear key such as `mosaic_crs`.
_Avoid_: bare `crs` as the mosaic story; assuming mosaic CRS equals the query CRS; calling any convenient AOI CRS the mosaic CRS

**Output CRS**:
An explicit ask for the **provider** to return pixels in a chosen CRS (server-side only, where supported). When unset, mosaic CRS follows the service/request contract for that protocol.
_Avoid_: implying a local client-side reproject; using “output CRS” for XYZ/WMTS native grids

**Ground resolution**:
The approximate real-world size of a pixel the caller wants for the job (e.g. “about 10 m”), stated in familiar units rather than protocol scale denominators.
_Avoid_: requiring matrix ids or ScaleDenominator on the happy path

## Access flow

**Design**:
The validated binding that solves the job’s constraints for one AOI — ready request, service, fetch policy, and plan — shared before preview and create diverge. Internal only.
_Avoid_: array entry; entry builder; exposing design as a public caller type

**Plan**:
How fetch and assemble will be done for one AOI: the tile list, optionally carrying a native mosaic extent and grid shape when the protocol uses a fixed indexed grid.
_Avoid_: calling the preflight summary a plan; keeping two public “plan” nouns; inferred mosaic metadata as a second informal channel

**Preview**:
What the job will look like — a read-only preflight of counts, extents, CRS roles, and related summary — without assembling mosaic pixels.
_Avoid_: treating preview as a second compute path; calling preview “plan”

**Create**:
Bind fetch and error behaviour and produce the mosaic array (lazy by default) for one AOI.
_Avoid_: inventing a parallel “load then configure” habit as the primary path

**Compute**:
Materialise a lazy mosaic so tiles are actually fetched and assembled.
_Avoid_: treating compute as the place to change host ceilings or soft-fail policy

**Mosaic**:
The assembled, georeferenced array covering the mosaic extent for one create.
_Avoid_: mosaic as a synonym for a single tile; mosaic as an orchestration of many AOIs

## Tiles and placement

**Tile**:
One planned provider cell or response unit to fetch for the mosaic.
_Avoid_: using “tile” alone when meaning the whole mosaic; conflating provider tiles with Dask chunks unless both are meant

**Decoded tile**:
Pixel values obtained from a successful tile response, before placement onto the expected geometry.
_Avoid_: treating decode as georeferencing the mosaic

**Request geometry**:
The spatial shape and CRS expected for a single tile within the mosaic (extent and pixel size the response should fill).
_Avoid_: “request” alone (ambiguous with the whole-mosaic request or the HTTP call)

**Request-shaped array**:
A tile’s pixels arranged to match that tile’s request geometry — exact fit, coverage-padded place, or rejected when alignment cannot be established.
_Avoid_: canvas (implementation metaphor); silent stretch or upsample to force a fit

**Placement**:
The step that turns a decoded tile plus its request geometry into a request-shaped array.
_Avoid_: placement as soft-fail fill; placement as mosaic-wide CRS warping

**Coverage pad**:
Nodata outside the returned coverage when a successful response is clipped smaller than the request geometry, after the usable pixels are placed.
_Avoid_: soft-fail; calling every edge NaN a fetch failure

**Soft-fail**:
Opt-in filling of a whole tile after exhausted tile-local fetch failures, instead of aborting the mosaic.
_Avoid_: coverage pad; default hard-fail behaviour; decode or auth failures disguised as soft-fail

## Host access

**Fetch policy**:
Rules, bound when the design is formed, for how politely and concurrently tiles are fetched for a mosaic.
_Avoid_: changing host ceilings at compute time; promising a cross-process global host lock the library does not own

**Fetch preset**:
A reusable bundle of host-oriented fetch defaults composed onto a service configuration.
_Avoid_: growing separate host-specific create methods for operational folklore
