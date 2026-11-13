#!/usr/bin/env python3
"""Guard VCR cassette size, secret headers, and request/response integrity.

Fails CI when a cassette grows past the size budget, when filtered secret
headers appear in recorded YAML, or when a cassette's request URI / response
body no longer matches the ``tilearray_cassette`` integrity stamp.

Hand-editing a request URI while leaving the old response (as in #165 / #177)
fails the stamp check. Re-record with VCR, then run
``python scripts/check_cassettes.py --stamp`` to refresh hashes.

``--stamp`` appends / replaces only the ``tilearray_cassette`` block so the
recorded interactions stay byte-stable (no full-file YAML rewrite).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Keep individual cassettes small enough for sdists and reviews. The USGS
# multipart fixture was previously ~6 MB; after shrinking it stays well under.
DEFAULT_MAX_BYTES = 256 * 1024

SECRET_HEADER_RE = re.compile(
    r"(?im)^\s*(authorization|x-api-key|x-auth-token|cookie|set-cookie)\s*:",
)

CASSETTE_GLOB = "tests/**/cassettes/**/*.yaml"
META_KEY = "tilearray_cassette"
_META_BLOCK_RE = re.compile(rf"(?ms)^{re.escape(META_KEY)}:\n(?:^[ \t].*\n|^[ \t]*\n)*")


def iter_cassettes(root: Path) -> list[Path]:
    return sorted(root.glob(CASSETTE_GLOB))


def check_size(path: Path, max_bytes: int) -> str | None:
    size = path.stat().st_size
    if size > max_bytes:
        return f"{path}: {size} bytes exceeds limit of {max_bytes} bytes"
    return None


def check_secret_headers(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = SECRET_HEADER_RE.search(text)
    if match:
        return (
            f"{path}: secret-bearing header {match.group(1)!r} found in cassette "
            "(filter via tests/conftest.py vcr_config.filter_headers)"
        )
    return None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _response_body_bytes(body: Any) -> bytes:
    if body is None:
        return b""
    if isinstance(body, dict):
        # VCR stores ``body: {string: ...}`` or ``body: {base64_string: ...}``.
        if "string" in body:
            raw = body["string"]
            if isinstance(raw, bytes):
                return raw
            return str(raw).encode("utf-8")
        if "base64_string" in body:
            return str(body["base64_string"]).encode("utf-8")
    if isinstance(body, bytes):
        return body
    return str(body).encode("utf-8")


def _interaction_digests(interaction: dict[str, Any]) -> tuple[str, str]:
    request = interaction.get("request") or {}
    response = interaction.get("response") or {}
    uri = str(request.get("uri") or "").encode("utf-8")
    body = _response_body_bytes(response.get("body"))
    return _sha256_bytes(uri), _sha256_bytes(body)


def _load_cassette(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: cassette root must be a mapping")
    return data


def check_integrity(path: Path) -> str | None:
    try:
        data = _load_cassette(path)
    except ValueError as exc:
        return str(exc)

    meta = data.get(META_KEY)
    if not isinstance(meta, dict):
        return (
            f"{path}: missing {META_KEY!r} integrity stamp — re-record then run "
            "`python scripts/check_cassettes.py --stamp`"
        )

    interactions = data.get("interactions")
    if not isinstance(interactions, list) or not interactions:
        return f"{path}: no interactions to verify"

    stamped = meta.get("interactions")
    if not isinstance(stamped, list) or len(stamped) != len(interactions):
        return (
            f"{path}: {META_KEY}.interactions length "
            f"{0 if not isinstance(stamped, list) else len(stamped)} "
            f"!= cassette interactions ({len(interactions)})"
        )

    for index, (interaction, stamp) in enumerate(zip(interactions, stamped)):
        if not isinstance(stamp, dict):
            return f"{path}: interaction {index} stamp is not a mapping"
        uri_hash, body_hash = _interaction_digests(interaction)
        expected_uri = stamp.get("request_uri_sha256")
        expected_body = stamp.get("response_body_sha256")
        if uri_hash != expected_uri:
            return (
                f"{path}: interaction {index} request URI hash mismatch "
                f"(cassette was edited without re-recording; got {uri_hash}, "
                f"stamp {expected_uri}). Re-record live, then --stamp."
            )
        if body_hash != expected_body:
            return (
                f"{path}: interaction {index} response body hash mismatch "
                f"(got {body_hash}, stamp {expected_body}). "
                "Re-record live, then --stamp."
            )
    return None


def _meta_block(interactions: list[dict[str, Any]]) -> str:
    recorded_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    lines = [
        f"{META_KEY}:",
        f"  recorded_at: '{recorded_at}'",
        "  interactions:",
    ]
    for interaction in interactions:
        uri_hash, body_hash = _interaction_digests(interaction)
        lines.append(f"  - request_uri_sha256: {uri_hash}")
        lines.append(f"    response_body_sha256: {body_hash}")
    return "\n".join(lines) + "\n"


def stamp_cassette(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    data = _load_cassette(path)
    interactions = data.get("interactions")
    if not isinstance(interactions, list) or not interactions:
        raise ValueError(f"{path}: no interactions to stamp")

    block = _meta_block(interactions)
    if _META_BLOCK_RE.search(text):
        text = _META_BLOCK_RE.sub(block, text, count=1)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += block
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: cwd)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Per-cassette size budget (default: {DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="Write/refresh tilearray_cassette integrity stamps from current YAML",
    )
    args = parser.parse_args(argv)

    cassettes = iter_cassettes(args.root)
    if not cassettes:
        print(f"No cassettes found under {args.root / 'tests'}", file=sys.stderr)
        return 1

    if args.stamp:
        for path in cassettes:
            stamp_cassette(path)
            print(f"stamped {path}")
        print(f"OK: stamped {len(cassettes)} cassette(s)")
        return 0

    errors: list[str] = []
    for path in cassettes:
        for check in (
            check_size(path, args.max_bytes),
            check_secret_headers(path),
            check_integrity(path),
        ):
            if check:
                errors.append(check)

    if errors:
        print("Cassette checks failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(cassettes)} cassette(s) within {args.max_bytes} bytes; "
        "no secret headers; integrity stamps match"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
