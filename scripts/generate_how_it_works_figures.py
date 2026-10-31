#!/usr/bin/env python3
"""
Generate offline stage diagrams for docs/how-it-works.md.

No network. Writes PNGs under docs/_static/guides/.

Prefer the umbrella Make target (also runs live usage-guide figures):

    make docs-artifacts

Direct invoke (diagrams only, offline):

    uv run --extra docs python scripts/generate_how_it_works_figures.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "_static" / "guides"

BG = "#f7f8fa"
INK = "#1a1a1a"
MUTED = "#556677"
LINE = "#2c3e50"
ACCENT = "#4a6fa5"
OK = "#2f6f4e"
WARN = "#a15c2f"
TILE = "#d9e4f2"
HOST = "#e8eef6"


def _fig(w: float, h: float):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    return fig, ax


def _box(ax, x, y, w, h, *, fc="#ffffff", ec=LINE, lw=1.2, rs=0.1):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0.02,rounding_size={rs}",
        linewidth=lw,
        edgecolor=ec,
        facecolor=fc,
    )
    ax.add_patch(patch)
    return patch


def _arrow(ax, x0, y0, x1, y1, *, color=ACCENT):
    ax.annotate(
        "",
        xy=(x1, y1),
        xytext=(x0, y0),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.6),
    )


def _save(fig, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=140, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print("wrote", path.name)
    return path


def stage_plan() -> None:
    fig, ax = _fig(9.5, 4.6)
    ax.set_title("1 · Plan tiles from the AOI", fontsize=13, color=INK, pad=8)

    # AOI map panel
    _box(ax, 0.35, 0.55, 4.5, 3.5, fc="#ffffff")
    ax.text(2.6, 3.8, "Your bbox (AOI)", ha="center", fontsize=10, color=INK)

    # Draw 3x4 tile grid with AOI outline overlapping
    origin_x, origin_y = 0.7, 0.9
    tw, th = 0.95, 0.7
    for r in range(4):
        for c in range(3):
            x = origin_x + c * tw
            y = origin_y + (3 - r) * th
            rect = Rectangle(
                (x, y),
                tw - 0.05,
                th - 0.05,
                linewidth=1.0,
                edgecolor=ACCENT,
                facecolor=TILE,
            )
            ax.add_patch(rect)
            ax.text(
                x + (tw - 0.05) / 2,
                y + (th - 0.05) / 2,
                f"({r},{c})",
                ha="center",
                va="center",
                fontsize=8,
                color=MUTED,
            )

    # AOI rectangle spanning subset of tiles
    aoi = Rectangle(
        (origin_x + 0.35, origin_y + 0.55),
        2.2,
        2.0,
        linewidth=2.2,
        edgecolor=WARN,
        facecolor="none",
        linestyle="--",
    )
    ax.add_patch(aoi)
    ax.text(2.2, 3.35, "AOI", color=WARN, fontsize=9, fontweight="bold")

    # Request list
    _box(ax, 5.2, 0.55, 4.0, 3.5, fc="#ffffff")
    ax.text(7.2, 3.8, "TileRequest list", ha="center", fontsize=10, color=INK)
    requests = [
        "GET …/tile/12/2048/1361.png",
        "GET …/tile/12/2049/1361.png",
        "GET …/tile/12/2048/1362.png",
        "… N requests for this AOI",
    ]
    for i, line in enumerate(requests):
        y = 3.25 - i * 0.55
        _box(ax, 5.45, y - 0.22, 3.5, 0.45, fc=HOST, ec=ACCENT, lw=1.0)
        ax.text(7.2, y, line, ha="center", va="center", fontsize=8, color=INK)

    ax.text(
        4.75,
        0.22,
        "Adapters turn bbox + chunk/grid into ordered TileRequests (XYZ/WMTS grids, WCS subsets).",
        ha="center",
        fontsize=8.5,
        color=MUTED,
        style="italic",
    )
    _arrow(ax, 4.9, 2.3, 5.15, 2.3)
    _save(fig, "how-it-works-01-plan.png")


def stage_fetch() -> None:
    fig, ax = _fig(9.8, 4.8)
    ax.set_title(
        "2 · Polite concurrent fetch (threaded httpx, not asyncio)",
        fontsize=13,
        color=INK,
        pad=8,
    )

    # Dask workers
    _box(ax, 0.3, 2.6, 3.1, 1.7, fc="#ffffff")
    ax.text(1.85, 4.05, "Dask thread pool", ha="center", fontsize=10, color=INK)
    for i, label in enumerate(["T0", "T1", "T2", "T3"]):
        x = 0.55 + i * 0.7
        _box(ax, x, 2.85, 0.55, 0.9, fc=TILE, ec=ACCENT)
        ax.text(x + 0.275, 3.3, label, ha="center", va="center", fontsize=8)

    # Fetch gate
    _box(ax, 3.8, 2.4, 2.6, 2.1, fc="#ffffff", ec=WARN, lw=1.6)
    ax.text(5.1, 4.2, "Fetch gate", ha="center", fontsize=10, color=WARN)
    ax.text(
        5.1,
        3.55,
        "semaphore / AIMD\nmax_concurrent\n(+ optional rate limit)",
        ha="center",
        va="center",
        fontsize=8.5,
        color=INK,
    )
    ax.text(
        5.1,
        2.65,
        "pressure → backoff\nbreaker → floor",
        ha="center",
        fontsize=8,
        color=MUTED,
    )

    # Host
    _box(ax, 7.0, 2.6, 2.5, 1.7, fc=HOST)
    ax.text(8.25, 4.05, "Tile host", ha="center", fontsize=10, color=INK)
    for i in range(3):
        y = 3.55 - i * 0.35
        ax.plot([7.25, 9.2], [y, y], color=ACCENT, lw=1.2)
        ax.text(8.25, y + 0.08, f"HTTP GET #{i+1}", ha="center", fontsize=7, color=MUTED)

    _arrow(ax, 3.45, 3.4, 3.75, 3.4)
    _arrow(ax, 6.45, 3.4, 6.95, 3.4)

    # Legend strip
    _box(ax, 0.3, 0.35, 9.2, 1.7, fc="#ffffff")
    ax.text(0.55, 1.7, "Code-accurate behaviour", fontsize=9, color=INK, fontweight="bold")
    bullets = [
        "Sync httpx.Client + threading — Dask schedules tile tasks; the gate caps in-flight HTTP.",
        "Fixed ceiling (e.g. OSM) or AIMD under max_concurrent (e.g. EA DSP) + optional circuit breaker.",
        "Retries (Tenacity); Retry-After honoured; hard failure → NetworkError (no silent NaN holes).",
    ]
    for i, b in enumerate(bullets):
        ax.text(0.55, 1.3 - i * 0.35, f"•  {b}", fontsize=8, color=MUTED, va="center")

    _save(fig, "how-it-works-02-fetch.png")


def stage_decode() -> None:
    fig, ax = _fig(9.6, 3.8)
    ax.set_title("3 · Shared decode → float32 arrays", fontsize=13, color=INK, pad=8)

    stages = [
        (0.35, "HTTP bytes\nPNG / JPEG /\nGeoTIFF / multipart", HOST),
        (3.3, "tilearray.decode\nunwrap · read\nband policy", "#ffffff"),
        (6.25, "ndarray\nfloat32\n(y, x[, band])", TILE),
    ]
    for x, label, fc in stages:
        _box(ax, x, 1.1, 2.55, 2.0, fc=fc)
        ax.text(x + 1.275, 2.1, label, ha="center", va="center", fontsize=9, color=INK)

    _arrow(ax, 2.95, 2.1, 3.25, 2.1)
    _arrow(ax, 5.9, 2.1, 6.2, 2.1)

    ax.text(
        4.8,
        0.45,
        "Same decode path for WCS, WMS, WMTS, and XYZ — JPEG/PNG preserve bands; GeoTIFF elevation uses first band.",
        ha="center",
        fontsize=8.5,
        color=MUTED,
        style="italic",
    )
    _save(fig, "how-it-works-03-decode.png")


def stage_mosaic() -> None:
    fig, ax = _fig(9.8, 4.6)
    ax.set_title(
        "4 · Dask mosaic assemble (north-up DataArray)",
        fontsize=13,
        color=INK,
        pad=8,
    )

    # Delayed tiles
    ax.text(2.3, 4.15, "Delayed tile blocks", ha="center", fontsize=10, color=INK)
    rng = np.random.default_rng(0)
    for r in range(2):
        for c in range(3):
            x = 0.5 + c * 1.25
            y = 2.35 - r * 1.15
            _box(ax, x, y, 1.05, 0.95, fc=TILE, ec=ACCENT)
            # mini synthetic texture
            data = rng.random((8, 8))
            ax.imshow(
                data,
                extent=(x + 0.12, x + 0.93, y + 0.12, y + 0.83),
                cmap="terrain",
                vmin=0,
                vmax=1,
                zorder=2,
            )
            ax.text(
                x + 0.525,
                y + 0.08,
                f"delayed[{r},{c}]",
                ha="center",
                fontsize=6.5,
                color=MUTED,
            )

    _arrow(ax, 4.35, 2.5, 5.0, 2.5)

    # Assembled array
    _box(ax, 5.2, 0.7, 4.2, 3.4, fc="#ffffff")
    ax.text(7.3, 3.85, "xarray.DataArray", ha="center", fontsize=10, color=INK)
    mosaic = np.linspace(0.2, 0.9, 24 * 36).reshape(24, 36)
    mosaic = mosaic + 0.08 * np.sin(np.linspace(0, 6, 24))[:, None]
    ax.imshow(
        mosaic,
        extent=(5.55, 8.95, 1.15, 3.45),
        cmap="terrain",
        origin="upper",
        zorder=2,
    )
    ax.text(7.25, 1.0, "north-up · coords · attrs", ha="center", fontsize=8, color=MUTED)
    ax.text(
        7.25,
        0.55,
        "shape known up front; bytes load when computed",
        ha="center",
        fontsize=8,
        color=MUTED,
        style="italic",
    )
    _save(fig, "how-it-works-04-mosaic.png")


def stage_lazy_compute_cache() -> None:
    fig, ax = _fig(10.0, 5.0)
    ax.set_title(
        "5 · Lazy graph · compute · optional disk cache",
        fontsize=13,
        color=INK,
        pad=8,
    )

    panels = [
        (
            0.3,
            "A · Lazy (default)",
            "create_array(...)\n→ Dask graph only\nshape/coords/attrs ready\npixels not all in RAM",
            OK,
        ),
        (
            3.5,
            "B · Compute",
            "compute_with_policy(da)\nor compute=True\nthread workers ↔\nfetch ceiling",
            ACCENT,
        ),
        (
            6.7,
            "C · Disk cache",
            "cache_dir=...\nwarm: read .tile\ncold: fetch + write\nstill polite to host",
            WARN,
        ),
    ]
    for x, title, body, ec in panels:
        _box(ax, x, 2.0, 2.95, 2.55, fc="#ffffff", ec=ec, lw=1.6)
        ax.text(x + 1.475, 4.2, title, ha="center", fontsize=10, color=ec, fontweight="bold")
        ax.text(x + 1.475, 3.05, body, ha="center", va="center", fontsize=8.5, color=INK)

    # Flow under panels
    _box(ax, 0.3, 0.35, 9.35, 1.4, fc="#ffffff")
    ax.text(0.55, 1.4, "API map", fontsize=9, color=INK, fontweight="bold")
    lines = [
        "load_array(...) ≈ create_array(..., compute=True)",
        "da.compute() works but may under-use a high AIMD ceiling (CPU-bound worker default)",
        "Prefer compute_with_policy for multi-tile mosaics so Dask threads match FetchPolicy.max_concurrent",
    ]
    for i, line in enumerate(lines):
        ax.text(0.55, 1.05 - i * 0.3, f"•  {line}", fontsize=8, color=MUTED, va="center")

    _save(fig, "how-it-works-05-lazy-compute-cache.png")


def stage_downstream() -> None:
    fig, ax = _fig(9.8, 4.2)
    ax.set_title(
        "6 · Same array API → plot · analyse · ML",
        fontsize=13,
        color=INK,
        pad=8,
    )

    _box(ax, 0.35, 1.3, 2.6, 2.3, fc=TILE, ec=ACCENT)
    ax.text(1.65, 3.3, "DataArray", ha="center", fontsize=10, color=INK)
    ax.text(
        1.65,
        2.3,
        "lazy or\ncomputed\nchunked",
        ha="center",
        va="center",
        fontsize=9,
        color=MUTED,
    )

    targets = [
        (3.5, "Plot", "matplotlib\nholoviews\n…"),
        (5.55, "Analyse", "reductions\nterrain\nresample"),
        (7.6, "ML", "chips\nbatches\ntensors"),
    ]
    for x, title, body in targets:
        _box(ax, x, 1.3, 1.85, 2.3, fc="#ffffff")
        ax.text(x + 0.925, 3.2, title, ha="center", fontsize=10, color=ACCENT, fontweight="bold")
        ax.text(x + 0.925, 2.2, body, ha="center", va="center", fontsize=9, color=INK)

    _arrow(ax, 3.0, 2.45, 3.45, 2.45)
    _arrow(ax, 5.4, 2.45, 5.5, 2.45)
    _arrow(ax, 7.45, 2.45, 7.55, 2.45)

    ax.text(
        4.9,
        0.55,
        "Laptop or bigger machine: same create_array path — scale via bbox, chunks, and fetch policy.",
        ha="center",
        fontsize=8.5,
        color=MUTED,
        style="italic",
    )
    _save(fig, "how-it-works-06-downstream.png")


def refresh_overview() -> None:
    """Keep the overview strip in sync with the stage numbering."""
    fig, ax = _fig(10.2, 3.4)
    ax.set_title("tilearray mental model — end to end", fontsize=12, color=INK, pad=8)
    stages = [
        (0.35, "1 Plan\nAOI → requests"),
        (2.35, "2 Fetch\nthreaded + gate"),
        (4.35, "3 Decode\nbytes → array"),
        (6.35, "4 Mosaic\nDask → xarray"),
        (8.35, "5–6 Use\nlazy / compute"),
    ]
    for x, label in stages:
        _box(ax, x, 1.05, 1.7, 1.45, fc="#ffffff")
        ax.text(x + 0.85, 1.78, label, ha="center", va="center", fontsize=9, color=INK)
    for i in range(len(stages) - 1):
        x0 = stages[i][0] + 1.7
        x1 = stages[i + 1][0]
        _arrow(ax, x0 + 0.05, 1.78, x1 - 0.05, 1.78)
    ax.text(
        5.1,
        0.4,
        "One API on a laptop or a bigger machine — scale is chunk count + fetch policy, not a new client.",
        ha="center",
        fontsize=8.5,
        color=MUTED,
        style="italic",
    )
    _save(fig, "how-it-works-pipeline.png")


def main() -> int:
    stage_plan()
    stage_fetch()
    stage_decode()
    stage_mosaic()
    stage_lazy_compute_cache()
    stage_downstream()
    refresh_overview()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
