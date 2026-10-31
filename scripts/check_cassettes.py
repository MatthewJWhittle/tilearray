#!/usr/bin/env python3
"""Guard VCR cassette size and secret-bearing response/request headers.

Fails CI when a cassette grows past the size budget or when filtered secret
headers appear in recorded YAML (Authorization, cookies, API keys, etc.).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Keep individual cassettes small enough for sdists and reviews. The USGS
# multipart fixture was previously ~6 MB; after shrinking it stays well under.
DEFAULT_MAX_BYTES = 256 * 1024

SECRET_HEADER_RE = re.compile(
    r"(?im)^\s*(authorization|x-api-key|x-auth-token|cookie|set-cookie)\s*:",
)

CASSETTE_GLOB = "tests/**/cassettes/**/*.yaml"


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
    args = parser.parse_args(argv)

    cassettes = iter_cassettes(args.root)
    if not cassettes:
        print(f"No cassettes found under {args.root / 'tests'}", file=sys.stderr)
        return 1

    errors: list[str] = []
    for path in cassettes:
        for check in (check_size(path, args.max_bytes), check_secret_headers(path)):
            if check:
                errors.append(check)

    if errors:
        print("Cassette checks failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(cassettes)} cassette(s) within {args.max_bytes} bytes; no secret headers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
