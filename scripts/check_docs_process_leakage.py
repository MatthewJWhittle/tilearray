#!/usr/bin/env python3
"""Fail if user-facing docs reintroduce process-leakage phrases.

Scans README.md and docs/**/*.md. Allowlisted paths may mention the phrases
when documenting the writing filter itself. See docs/docs-writing-principles.md.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Phrases that usually mean milestone / agent / stage-gate chatter leaked
# into PyPI-reader docs. Keep this list short and high-signal.
DENYLIST = (
    re.compile(r"soft-exit", re.IGNORECASE),
    re.compile(r"stage-gate", re.IGNORECASE),
    re.compile(r"we decided", re.IGNORECASE),
    re.compile(r"feeds 0\.", re.IGNORECASE),
)

ALLOWLIST = {
    Path("docs/docs-writing-principles.md"),
}


def iter_targets() -> list[Path]:
    files = [ROOT / "README.md"]
    docs = ROOT / "docs"
    files.extend(sorted(docs.rglob("*.md")))
    return [p for p in files if p.is_file()]


def main() -> int:
    hits: list[str] = []
    for path in iter_targets():
        rel = path.relative_to(ROOT)
        if rel in ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            for pattern in DENYLIST:
                if pattern.search(line):
                    hits.append(f"{rel}:{i}: matched /{pattern.pattern}/ — {line.strip()}")
    if hits:
        print("Process-leakage phrases found in user-facing docs:", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        print(
            "\nRemove the chatter or move it to CONTRIBUTING / building-docs. "
            "Filter: docs/docs-writing-principles.md",
            file=sys.stderr,
        )
        return 1
    print(f"OK: no process-leakage phrases in {len(iter_targets()) - len(ALLOWLIST)} scanned docs files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
