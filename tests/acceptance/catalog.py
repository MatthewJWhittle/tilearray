"""Machine-readable scenario catalogue for community acceptance (#97).

Status vocabulary (baseline against public API at suite authorship):

* ``pass`` — current API already satisfies the required behaviour offline.
* ``fail`` — current API runs but produces incorrect georef / pixels / requests.
* ``unsupported`` — public API cannot express the required CRS/host/capability yet.
* ``user_config_error`` — invalid caller input; library should reject early.
* ``correct_rejection`` — library correctly refuses an unsupported promise.
* ``not_reproduced`` — source claim not exercised here (optional live only).

Do not treat ``fail`` / ``unsupported`` as regressions until an owning issue
claims the scenario and CI asserts the desired end state without xfail.
"""

from __future__ import annotations

from typing import Literal, TypedDict

BaselineStatus = Literal[
    "pass",
    "fail",
    "unsupported",
    "user_config_error",
    "correct_rejection",
    "not_reproduced",
]


class Scenario(TypedDict):
    id: str
    title: str
    source_url: str
    source_date: str
    user_goal: str
    sample_parameters: dict[str, object]
    original_failure: str
    underlying_cause: str
    expected_outcome: str
    evidence_vs_inference: str
    baseline: BaselineStatus
    baseline_notes: str
    owning_issues: list[int]
    fixture_dir: str


SCENARIOS: list[Scenario] = [
    {
        "id": "S1",
        "title": "Norwegian Polar Institute WMTS tile bounds (EPSG:25833)",
        "source_url": (
            "https://gis.stackexchange.com/questions/484520/"
            "getting-tile-bounds-for-epsg25833-wmts-tile-using-python"
        ),
        "source_date": "2024-08-03",
        "user_goal": (
            "Given WMTS tile 17/31066/133128 on EPSG:25833, derive exact "
            "georeferenced bounds from TileMatrix metadata."
        ),
        "sample_parameters": {
            "rest_tile_url": (
                "https://geodata.npolar.no/arcgis/rest/services/Basisdata/"
                "NP_Ortofoto_Svalbard_WMTS_25833/MapServer/WMTS/tile/1.0.0/"
                "Basisdata_NP_Ortofoto_Svalbard_WMTS_25833/default/default028mm/"
                "17/31066/133128"
            ),
            "tile_matrix": "17",
            "tile_row": 31066,
            "tile_col": 133128,
            "crs": "EPSG:25833",
            "scale_denominator": 590.588978796931,
            "top_left_corner": [-5120900.0, 9998100.0],
            "tile_size": 256,
            "approximate_user_bbox_epsg25833": [
                514863.0,
                8682927.0,
                514905.0,
                8682970.0,
            ],
        },
        "original_failure": (
            "User had tile indices + approximate hand-drawn bbox; needed "
            "exact bounds from matrix maths (not from a client library)."
        ),
        "underlying_cause": (
            "WMTS returns display tiles; georef must come from TileMatrix "
            "ScaleDenominator / TopLeftCorner."
        ),
        "expected_outcome": (
            "Array (or planned tile) exposes row/col 31066/133128, 256×256, "
            "transform/extent matching independent golden bounds; axis units "
            "metres in EPSG:25833. Do not treat the approximate GIS-SE polygon "
            "as geospatial truth."
        ),
        "evidence_vs_inference": (
            "Direct: matrix numbers and tile indices from the post/answer. "
            "Inference: tilearray should eventually accept EPSG:25833 (#92) "
            "and plan native-CRS matrices (#93)."
        ),
        "baseline": "unsupported",
        "baseline_notes": (
            "CRS enum only has 4326/3857/32633/27700; EPSG:25833 rejected at "
            "the door. Independent golden bounds match the published answer."
        ),
        "owning_issues": [92, 93],
        "fixture_dir": "fixtures/s1_npi_wmts",
    },
    {
        "id": "S2",
        "title": "WGS84 WMTS tile without slippy EPSG:3857 fallback",
        "source_url": (
            "https://gis.stackexchange.com/questions/323374/"
            "getting-spatially-referenced-tiles-from-wmts"
        ),
        "source_date": "2019-05-20",
        "user_goal": (
            "Download a WGS84-matrix WMTS tile (matrix 16, row 20480, "
            "col 32001) as a spatially referenced array."
        ),
        "sample_parameters": {
            "tile_matrix_set": "WGS84",
            "tile_matrix": "16",
            "tile_row": 20480,
            "tile_col": 32001,
            "format": "image/tif",
            "supported_crs": "urn:ogc:def:crs:EPSG::4326",
            "synthetic_scale_denominator": 4265.459167669,
            "top_left_corner_caps_order": [90.0, -180.0],
            "top_left_interpreted_lon_lat": [-180.0, 90.0],
        },
        "original_failure": ("OWSLib gettile wrote a TIFF with no georeferencing."),
        "underlying_cause": (
            "Client must attach matrix-derived coords; WMTS tiles are pictures."
        ),
        "expected_outcome": (
            "xarray output georeferenced on the native WGS84 matrix grid; no "
            "incorrect WebMercator/slippy planning for this matrix."
        ),
        "evidence_vs_inference": (
            "Direct: layer/matrix identifiers and row/col from the post. "
            "Synthetic ScaleDenominator chosen for offline determinism "
            "(post showed a nearby zoom example, not exact level-16 numbers)."
        ),
        "baseline": "fail",
        "baseline_notes": (
            "EPSG:4326 is accepted, but matrix planning treats TopLeftCorner "
            "token order as x/y without geographic metres-per-unit, and can "
            "select the wrong tile indices for NW-origin degree matrices."
        ),
        "owning_issues": [93, 60],
        "fixture_dir": "fixtures/s2_wgs84_wmts",
    },
    {
        "id": "S3",
        "title": "Many custom-extent WMTS patches via ordinary bbox loop",
        "source_url": (
            "https://gis.stackexchange.com/questions/282675/"
            "download-custom-extent-from-wmts"
        ),
        "source_date": "2018-05-12",
        "user_goal": (
            "Automate thousands of bbox patches from WMTS without a WMS-like "
            "server-side GetMap."
        ),
        "sample_parameters": {
            "bbox_count": 3,
            "note": "Suite uses a tiny noncontiguous offline set, not 10k live tiles.",
        },
        "original_failure": (
            "WMTS only accepts matrix row/col; QGIS Save As does not scale."
        ),
        "underlying_cause": "Need client-side bbox→tiles→georeferenced mosaic.",
        "expected_outcome": (
            "Per-bbox array has exact spatial coords and predictable size; "
            "disk cache reuses overlapping source tiles; ordinary Python loop "
            "is enough (no ML orchestration engine)."
        ),
        "evidence_vs_inference": (
            "Direct: scale of 'tens of thousands' of patches. Inference: "
            "documented loop + cache reuse matches current product stance."
        ),
        "baseline": "pass",
        "baseline_notes": (
            "Offline XYZ/WMTS-style fake service + cache_dir demonstrates "
            "loop + reuse. Full 10k orchestration remains out of scope (#87)."
        ),
        "owning_issues": [87, 61],
        "fixture_dir": "fixtures/s3_multi_bbox",
    },
    {
        "id": "S4",
        "title": "GeoServer WCS returns smaller/offset coverage at edges",
        "source_url": (
            "https://gis.stackexchange.com/questions/482781/"
            "handle-or-disable-wcs-geoserver-trimmed-down-coverage-response-"
            "when-asking-dat"
        ),
        "source_date": "2024-06-01",
        "user_goal": (
            "Request fixed-size overlapping patches (e.g. 1700×1700, 4+ bands) "
            "even when the server trims to available coverage."
        ),
        "sample_parameters": {
            "requested_width": 64,
            "requested_height": 64,
            "returned_width": 48,
            "returned_height": 48,
            "band_count": 4,
            "offset_pixels_x": 8,
            "offset_pixels_y": 8,
        },
        "original_failure": (
            "GeoServer WCS returned a smaller coverage than requested near edges."
        ),
        "underlying_cause": "Server clips to coverage; client must not stretch.",
        "expected_outcome": (
            "Output keeps requested grid shape; partial coverage lands with "
            "correct pixel alignment; outside pixels are explicit nodata; "
            "invalid transforms fail loudly (no silent stretch/shift)."
        ),
        "evidence_vs_inference": (
            "Direct: trimmed response problem. Inference: exact 1700²/4-band "
            "sizes reduced to tiny offline fixtures with the same geometry."
        ),
        "baseline": "fail",
        "baseline_notes": (
            "Decode/assemble currently trusts returned raster shape; clipped "
            "responses are not yet aligned onto the request grid (#95/#94)."
        ),
        "owning_issues": [95, 94],
        "fixture_dir": "fixtures/s4_wcs_clipped",
    },
    {
        "id": "S5",
        "title": "WCS 2.0.1 GetCoverage subsets (local fake GeoServer)",
        "source_url": (
            "https://gis.stackexchange.com/questions/483928/"
            "how-to-create-wcs-2-0-1-getcoverage-request-using-owslib"
        ),
        "source_date": "2024-07-18",
        "user_goal": (
            "Build a correct WCS 2.0.1 GetCoverage with EPSG:32632 subsets, "
            "dimensions, and GeoTIFF MIME against a local GeoServer."
        ),
        "sample_parameters": {
            "crs": "EPSG:32632",
            "version": "2.0.1",
            "output_format": "image/tiff",
            "note": "Do not treat example.com placeholders as a live host.",
        },
        "original_failure": (
            "OWSLib requests timed out / failed while a manually built URL worked."
        ),
        "underlying_cause": (
            "Timeouts on large coverages; awkward WCS 2.x request construction."
        ),
        "expected_outcome": (
            "Generated subsets/axis order, width/height, content-type match "
            "captured sanitised params; timeouts/errors stay actionable."
        ),
        "evidence_vs_inference": (
            "Direct: EPSG:32632 + 2.0.1 + TIFF from the post. Inference: "
            "subset axis labels for UTM northing/easting until #92/#96 land."
        ),
        "baseline": "unsupported",
        "baseline_notes": (
            "EPSG:32632 is outside the CRS enum (#92). Format/URL/coverage_id "
            "error messaging already improved via #88."
        ),
        "owning_issues": [92, 96, 88],
        "fixture_dir": "fixtures/s5_wcs_201",
    },
    {
        "id": "S6",
        "title": "PDOK WMS misleading unknown layer from EPGS typo",
        "source_url": (
            "https://gis.stackexchange.com/questions/486554/"
            "unknown-layer-exception-when-downloading-wms-chip-with-owslib"
        ),
        "source_date": "2024-10-07",
        "user_goal": (
            "Download a PDOK ortho WMS chip in EPSG:28992 without chasing a "
            "false 'unknown layer' server error."
        ),
        "sample_parameters": {
            "invalid_crs": "EPGS:28992",
            "corrected_crs": "EPSG:28992",
            "public_layer_from_post": "Actueel_ortho25_jpeg",
        },
        "original_failure": (
            "Server returned unknown layer when the real bug was an EPGS typo."
        ),
        "underlying_cause": "User CRS typo + misleading server message.",
        "expected_outcome": (
            "Invalid CRS fails early with an actionable hint; corrected CRS "
            "builds GetMap with expected bbox/dims/MIME; never silently rewrite "
            "the caller's CRS string."
        ),
        "evidence_vs_inference": (
            "Direct: EPGS vs EPSG and layer name from the post. Inference: "
            "EPSG:28992 acceptance belongs to #92; typo messaging to #88/#96."
        ),
        "baseline": "user_config_error",
        "baseline_notes": (
            "EPGS:… is rejected early (ValidationError). Corrected EPSG:28992 "
            "is still unsupported by the four-value CRS enum. No silent rewrite."
        ),
        "owning_issues": [92, 88, 96],
        "fixture_dir": "fixtures/s6_pdok_wms",
    },
    {
        "id": "S7",
        "title": "SoilGrids-style WCS max width/height constraints",
        "source_url": (
            "https://gis.stackexchange.com/questions/451564/"
            "error-getting-soilgrid-image-with-python-owslib"
        ),
        "source_date": "2022-01-01",
        "user_goal": (
            "Fetch a large AOI from a WCS that enforces maximum request "
            "width/height without subpixel slivers."
        ),
        "sample_parameters": {
            "related_owslib_issue": "https://github.com/geopython/OWSLib/issues/853",
            "fake_max_width": 32,
            "fake_max_height": 32,
            "aoi_width_px": 64,
            "aoi_height_px": 64,
        },
        "original_failure": (
            "Large GetCoverage requests fail or hang under server constraints."
        ),
        "underlying_cause": (
            "Server max dimensions / memory; clients must tile below the cap."
        ),
        "expected_outcome": (
            "Small sub-requests assemble the AOI when planning supports it; "
            "no subpixel leftover tiles (#50). Unsupported auto-recovery fails "
            "with an actionable hint rather than a silent promise."
        ),
        "evidence_vs_inference": (
            "Direct: SoilGrids/OWSLib size/CRS pain. Inference: fake max-dim "
            "server stands in for SoilGrids without live scraping."
        ),
        "baseline": "pass",
        "baseline_notes": (
            "WCS planning with resolution already avoids subpixel leftovers "
            "(#50). Fake max-dim server + chunked plan covers the assemble path; "
            "universal auto-discovery of server caps remains #61/#96."
        ),
        "owning_issues": [61, 88, 50],
        "fixture_dir": "fixtures/s7_soilgrids_wcs",
    },
    {
        "id": "S8",
        "title": "Raster / image semantics (palette vs numeric)",
        "source_url": (
            "https://gis.stackexchange.com/questions/482781/"
            "handle-or-disable-wcs-geoserver-trimmed-down-coverage-response-"
            "when-asking-dat"
        ),
        "source_date": "2024-06-01",
        "user_goal": (
            "Keep analysis-ready values: palette index vs RGB, grayscale, "
            "RGBA, multiband TIFF, nodata, dtype, pixel-centre coords."
        ),
        "sample_parameters": {
            "note": (
                "Also informed by tilearray palette-PNG examples and common "
                "WMS/XYZ rendered-picture semantics."
            ),
        },
        "original_failure": (
            "Rendered WMS/XYZ imagery mistaken for original numerical values."
        ),
        "underlying_cause": "Protocol semantics (picture vs coverage).",
        "expected_outcome": (
            "Known-value fixtures decode with expected dtype/bands/nodata; "
            "docs/tests never treat rendered basemap pixels as coverage values."
        ),
        "evidence_vs_inference": (
            "Inference synthesised from S4 + library palette examples; fixtures "
            "are synthetic known values, not third-party copyrighted rasters."
        ),
        "baseline": "pass",
        "baseline_notes": (
            "Offline known-value decode path covers grayscale/RGB/RGBA/"
            "multiband/nodata. Palette-index preservation and fuller fidelity "
            "remain under #94."
        ),
        "owning_issues": [94],
        "fixture_dir": "fixtures/s8_raster_semantics",
    },
    {
        "id": "S9",
        "title": "Large mosaics under throttling / partial failure",
        "source_url": (
            "https://gis.stackexchange.com/questions/282675/"
            "download-custom-extent-from-wmts"
        ),
        "source_date": "2018-05-12",
        "user_goal": (
            "Survive 429/Retry-After, 5xx, single-tile failure, and corrupt "
            "bodies without stampeding the host."
        ),
        "sample_parameters": {
            "statuses": [429, 503, 500],
            "soft_fail_default": False,
        },
        "original_failure": (
            "Operational throttling and partial tile failures on large mosaics."
        ),
        "underlying_cause": "Host rate limits + imperfect networks.",
        "expected_outcome": (
            "Retries honour Retry-After; shared host ceiling; hard-fail default; "
            "opt-in partial fill; useful progress/preflight observability."
        ),
        "evidence_vs_inference": (
            "Inference from recurring OGC operational reports; behaviours "
            "already covered by fetch/soft-fail unit tests, re-indexed here."
        ),
        "baseline": "pass",
        "baseline_notes": (
            "429+Retry-After, AIMD ceiling, hard-fail default, and opt-in "
            "soft-fail are exercised offline. Distributed don't-stampede (#59) "
            "and richer preflight (#61) remain open."
        ),
        "owning_issues": [61, 59, 65],
        "fixture_dir": "fixtures/s9_resilience",
    },
]


def scenario_by_id(scenario_id: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario["id"] == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def owning_issue_index() -> dict[int, list[str]]:
    """Map GitHub issue number → scenario IDs it should eventually satisfy."""

    index: dict[int, list[str]] = {}
    for scenario in SCENARIOS:
        for issue in scenario["owning_issues"]:
            index.setdefault(issue, []).append(scenario["id"])
    return index
