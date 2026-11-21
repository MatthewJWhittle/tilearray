"""A hard tile failure must not leak fetch-gate slots (#213).

Before the fix, sibling fetches that acquired a slot and then saw the abort kept
their semaphore permit, so the next mosaic to the same host hung forever.
"""

from __future__ import annotations

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from PIL import Image

from tilearray import NetworkError, XYZConfig, compute_mosaic, create_array
from tilearray.fetch import TileFetcher

pytestmark = pytest.mark.unit


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (120, 120, 120)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


@pytest.fixture
def tile_server() -> Any:
    state: dict[str, Any] = {"fail_index": 2, "seen": []}
    body = _png()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return None

        def do_GET(self) -> None:  # noqa: N802
            seen = state["seen"]
            if self.path not in seen:
                seen.append(self.path)
            fail = (
                state["fail_index"] is not None
                and seen.index(self.path) == state["fail_index"]
            )
            payload, status, ctype = (
                (b"<ExceptionReport/>", 404, "application/xml")
                if fail
                else (body, 200, "image/png")
            )
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        server.server_close()


def _run_with_timeout(fn: Any, timeout: float) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def target() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    result["finished"] = not worker.is_alive()
    return result


def test_second_mosaic_after_hard_tile_failure_completes(tile_server: Any) -> None:
    server, state = tile_server
    port = server.server_address[1]
    config = XYZConfig.from_url(
        f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png",
        zoom=12,
        max_concurrent_requests=2,
    )
    bbox = (-0.2, 51.45, -0.05, 51.55)  # 3x3 tiles at z12

    def mosaic() -> Any:
        return compute_mosaic(
            create_array(
                config,
                bbox,
                bbox_crs="EPSG:4326",
                show_progress=False,
                rate_limit_per_second=None,
            )
        )

    first = _run_with_timeout(mosaic, 30)
    assert first["finished"], "first mosaic did not return"
    assert isinstance(first.get("error"), NetworkError)

    state["fail_index"] = None
    state["seen"].clear()
    second = _run_with_timeout(mosaic, 30)
    assert second["finished"], (
        "second mosaic to the same host hung (leaked fetch-gate slots)"
    )
    assert "error" not in second, second.get("error")
    assert second["value"].shape[:2] == (768, 768)
