"""Tiles whose band count differs from the probe tile (RGB among RGBA) (#214)."""

from __future__ import annotations

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import numpy as np
import pytest
from PIL import Image

from tilearray import ParseError, XYZConfig, compute_mosaic, create_array
from tilearray.fetch import TileFetcher

pytestmark = pytest.mark.unit

BBOX = (-0.2, 51.45, -0.05, 51.55)  # 3x3 tiles at z12


def _png(mode: str) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (256, 256), (120,) * len(mode)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _serve(modes: dict[int, str], default: str) -> Any:
    seen: list[str] = []
    bodies = {m: _png(m) for m in {default, *modes.values()}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return None

        def do_GET(self) -> None:  # noqa: N802
            if self.path not in seen:
                seen.append(self.path)
            payload = bodies[modes.get(seen.index(self.path), default)]
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _mosaic(server: Any) -> Any:
    port = server.server_address[1]
    config = XYZConfig.from_url(
        f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png",
        zoom=12,
        max_concurrent_requests=1,
    )
    return compute_mosaic(
        create_array(
            config,
            BBOX,
            bbox_crs="EPSG:4326",
            show_progress=False,
            rate_limit_per_second=None,
        )
    )


def test_rgb_tile_in_rgba_mosaic_gets_opaque_alpha() -> None:
    server = _serve({4: "RGB"}, default="RGBA")
    try:
        out = _mosaic(server)
    finally:
        server.shutdown()
    values = np.asarray(out.values)
    assert values.shape == (768, 768, 4)
    alpha = values[..., 3]
    assert set(np.unique(alpha).tolist()) == {
        120,
        255,
    }  # RGBA tiles keep alpha; RGB tile opaque


def test_rgba_tile_in_rgb_mosaic_drops_alpha() -> None:
    server = _serve({4: "RGBA"}, default="RGB")
    try:
        out = _mosaic(server)
    finally:
        server.shutdown()
    assert np.asarray(out.values).shape == (768, 768, 3)


def test_unreconcilable_band_count_raises_parse_error_naming_tile() -> None:
    server = _serve({4: "L"}, default="RGB")
    try:
        with pytest.raises(ParseError, match=r"band"):
            _mosaic(server)
    finally:
        server.shutdown()
