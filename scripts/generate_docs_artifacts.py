#!/usr/bin/env python3
"""
Generate live usage-guide mosaics for Sphinx docs from public tile hosts.

Uses polite fetch presets and small bboxes. Requires network. Not run in default CI.

Prefer the umbrella Make target (diagrams + live figures):

    make docs-artifacts

Direct invoke (live mosaics only):

    uv run --extra docs python scripts/generate_docs_artifacts.py
    uv run --extra docs python scripts/generate_docs_artifacts.py --only osm

Outputs land in docs/_static/guides/. Commit those files so ``make docs`` stays offline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tilearray import XYZConfig, compute_with_policy, create_array  # noqa: E402
from tilearray.fetch_presets import (  # noqa: E402
    EA_LIDAR_BENCH_BBOX,
    EA_LIDAR_COVERAGE_ID,
    EA_LIDAR_WCS_URL,
)
from tilearray.service import WCSConfig  # noqa: E402
from tilearray.types import CRS, Format  # noqa: E402

OUT_DIR = ROOT / "docs" / "_static" / "guides"

# Small central-London window (~one OSM tile at z16); keep polite.
OSM_LONDON_BBOX = (-0.005, 51.495, 0.005, 51.505)
OSM_ZOOM = 16

# NASA GIBS Blue Marble XYZ (path is z/y/x). Tiny Atlantic window at low zoom.
GIBS_TEMPLATE = (
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
    "BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/"
    "{z}/{y}/{x}.jpeg"
)
GIBS_BBOX = (-40.0, 20.0, -10.0, 45.0)  # mid-Atlantic / Iberia glance
GIBS_ZOOM = 3

# Slightly larger than the 64 m contract bbox so the guide shows terrain structure
# without a county-scale mosaic (~256 m → few tiles at 64 px / 1 m).
EA_GUIDE_BBOX = (431900.0, 382700.0, 432156.0, 382956.0)
EA_CHUNK = (64, 64)
EA_RESOLUTION = (1.0, 1.0)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def generate_osm(out_dir: Path) -> dict[str, Any]:
    print("OSM XYZ: fetching central London (polite preset)...")
    config = XYZConfig.for_openstreetmap(
        zoom=OSM_ZOOM,
        output_format=Format.PNG,
        chunk_size=(256, 256),
    )
    start = time.perf_counter()
    da = create_array(
        config,
        OSM_LONDON_BBOX,
        CRS.EPSG_4326,
        compute=True,
    )
    elapsed = time.perf_counter() - start

    # OSM raster tiles are often palette PNGs → single-band after decode today.
    fig, ax = plt.subplots(figsize=(5.5, 5.5))
    da.plot.imshow(ax=ax, cmap="gray", add_colorbar=False)
    ax.set_title("OpenStreetMap XYZ — central London")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal")
    fig.text(
        0.5,
        0.01,
        "Map data © OpenStreetMap contributors",
        ha="center",
        fontsize=8,
        style="italic",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    png = out_dir / "osm-london.png"
    fig.savefig(png, dpi=120)
    plt.close(fig)

    stats = {
        "host": "openstreetmap",
        "service": "XYZ",
        "bbox": list(OSM_LONDON_BBOX),
        "crs": "EPSG:4326",
        "zoom": OSM_ZOOM,
        "shape": list(da.shape),
        "dims": list(da.dims),
        "elapsed_s": round(elapsed, 3),
        "mean": float(np.nanmean(da.values)),
        "figure": png.name,
        "attribution": "© OpenStreetMap contributors",
        "note": "OSM PNGs are often palette mode; decoded mosaic is single-band today",
    }
    _write_json(out_dir / "osm-london.json", stats)
    print(f"  wrote {png.name} shape={da.shape} in {elapsed:.1f}s")
    return stats


def generate_gibs(out_dir: Path) -> dict[str, Any]:
    print("NASA GIBS XYZ: fetching Blue Marble glance (JPEG RGB)...")
    config = XYZConfig.from_url(
        GIBS_TEMPLATE,
        zoom=GIBS_ZOOM,
        output_format=Format.JPEG,
        chunk_size=(256, 256),
        max_concurrent_requests=2,
        rate_limit_per_second=2.0,
        headers={
            "User-Agent": "tilearray-docs/0.1 (+https://github.com/MatthewJWhittle/tilearray/issues)",
        },
    )
    start = time.perf_counter()
    da = create_array(config, GIBS_BBOX, CRS.EPSG_4326, compute=True)
    elapsed = time.perf_counter() - start

    fig, ax = plt.subplots(figsize=(6.0, 5.0))
    if da.ndim == 3 and "band" in da.dims and da.sizes["band"] >= 3:
        rgb = np.clip(np.asarray(da.values[..., :3]) / 255.0, 0, 1)
        ax.imshow(
            rgb,
            extent=[
                float(da.x.min()),
                float(da.x.max()),
                float(da.y.min()),
                float(da.y.max()),
            ],
            origin="upper",
        )
    else:
        da.plot.imshow(ax=ax, add_colorbar=False)
    ax.set_title("NASA GIBS Blue Marble — mid-Atlantic glance")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal")
    fig.text(
        0.5,
        0.01,
        "Imagery: NASA GIBS / BlueMarble_NextGeneration",
        ha="center",
        fontsize=8,
        style="italic",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    png = out_dir / "gibs-bluemarble.png"
    fig.savefig(png, dpi=120)
    plt.close(fig)

    stats = {
        "host": "nasa-gibs",
        "service": "XYZ",
        "bbox": list(GIBS_BBOX),
        "crs": "EPSG:4326",
        "zoom": GIBS_ZOOM,
        "shape": list(da.shape),
        "dims": list(da.dims),
        "elapsed_s": round(elapsed, 3),
        "mean": float(np.nanmean(da.values)),
        "figure": png.name,
        "template_note": "GIBS path is {z}/{y}/{x} (row before col)",
    }
    _write_json(out_dir / "gibs-bluemarble.json", stats)
    print(f"  wrote {png.name} shape={da.shape} in {elapsed:.1f}s")
    return stats


def generate_ea(out_dir: Path) -> dict[str, Any]:
    print("EA Lidar WCS: fetching small OSGB elevation window...")
    config = WCSConfig.for_ea_dsp(
        EA_LIDAR_WCS_URL,
        coverage_id=EA_LIDAR_COVERAGE_ID,
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        chunk_size=EA_CHUNK,
        resolution=EA_RESOLUTION,
    )
    start = time.perf_counter()
    da = create_array(config, EA_GUIDE_BBOX, CRS.EPSG_27700)
    elevation = compute_with_policy(da)
    elapsed = time.perf_counter() - start

    values = np.asarray(elevation.values, dtype=np.float64)
    finite = values[np.isfinite(values)]
    mean_z = float(finite.mean()) if finite.size else float("nan")
    min_z = float(finite.min()) if finite.size else float("nan")
    max_z = float(finite.max()) if finite.size else float("nan")

    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.2))
    im = axes[0].imshow(
        values,
        extent=[
            float(elevation.x.min()),
            float(elevation.x.max()),
            float(elevation.y.min()),
            float(elevation.y.max()),
        ],
        origin="upper",
        cmap="terrain",
    )
    axes[0].set_title("EA Lidar DTM (WCS)")
    axes[0].set_xlabel("easting (EPSG:27700)")
    axes[0].set_ylabel("northing (EPSG:27700)")
    axes[0].set_aspect("equal")
    fig.colorbar(im, ax=axes[0], fraction=0.046, pad=0.04, label="elevation (m)")

    axes[1].hist(finite, bins=40, color="#4a6fa5", edgecolor="white")
    axes[1].axvline(mean_z, color="#c0392b", linestyle="--", label=f"mean {mean_z:.1f} m")
    axes[1].set_title("Elevation distribution")
    axes[1].set_xlabel("elevation (m)")
    axes[1].set_ylabel("pixel count")
    axes[1].legend(frameon=False)

    fig.suptitle("Environment Agency Lidar composite DTM — small OSGB window", fontsize=11)
    fig.tight_layout()
    png = out_dir / "ea-lidar-elevation.png"
    fig.savefig(png, dpi=120)
    plt.close(fig)

    stats = {
        "host": "environment-agency-dsp",
        "service": "WCS",
        "coverage_id": EA_LIDAR_COVERAGE_ID,
        "bbox": list(EA_GUIDE_BBOX),
        "crs": "EPSG:27700",
        "chunk_size": list(EA_CHUNK),
        "resolution": list(EA_RESOLUTION),
        "shape": list(elevation.shape),
        "dims": list(elevation.dims),
        "elapsed_s": round(elapsed, 3),
        "mean_m": mean_z,
        "min_m": min_z,
        "max_m": max_z,
        "finite_frac": float(finite.size / values.size) if values.size else 0.0,
        "figure": png.name,
        "note": (
            "Contract-scale bbox near the documented EA bench window; "
            f"also see EA_LIDAR_BENCH_BBOX={EA_LIDAR_BENCH_BBOX}"
        ),
    }
    _write_json(out_dir / "ea-lidar-elevation.json", stats)
    print(
        f"  wrote {png.name} shape={elevation.shape} "
        f"mean={mean_z:.2f}m in {elapsed:.1f}s"
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=("osm", "ea", "gibs", "all"),
        default="all",
        help="Which artifact set to regenerate (default: all)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR,
        help=f"Output directory (default: {OUT_DIR})",
    )
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {}
    if args.only in ("osm", "all"):
        results["osm"] = generate_osm(out_dir)
    if args.only in ("gibs", "all"):
        results["gibs"] = generate_gibs(out_dir)
    if args.only in ("ea", "all"):
        results["ea"] = generate_ea(out_dir)

    manifest = {
        "generated_by": "scripts/generate_docs_artifacts.py",
        "artifacts": results,
    }
    _write_json(out_dir / "manifest.json", manifest)
    print(f"Done. Assets in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
