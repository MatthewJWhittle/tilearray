#!/usr/bin/env python3
"""Rerun the #226 mutation yardstick against fetch + planning.

Plants each known-surviving bug from issue #226 in turn, runs a scoped
offline pytest selection, and reports whether the suite kills the mutant.

Usage (from repo root, after ``uv sync --dev``)::

    uv run python scripts/check_planted_mutations.py
    uv run python scripts/check_planted_mutations.py --ids F1,F10,P2

Exit code 0 only when every selected mutant is killed.
"""

from __future__ import annotations

import argparse
import atexit
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

# (path, original_text) so a killed run cannot leave a planted bug in the tree.
_PENDING_RESTORE: list[tuple[Path, str]] = []


def _restore_pending() -> None:
    while _PENDING_RESTORE:
        path, original = _PENDING_RESTORE.pop()
        path.write_text(original, encoding="utf-8")


atexit.register(_restore_pending)

ROOT = Path(__file__).resolve().parents[1]
FETCH = ROOT / "src" / "tilearray" / "fetch.py"
PLANNING = ROOT / "src" / "tilearray" / "_planning.py"

FETCH_TESTS = [
    "tests/unit/test_fetch_resilience.py",
    "tests/unit/test_fetch_concurrency.py",
]
PLANNING_TESTS = [
    "tests/unit/test_array.py::test_array_request_rejects_nonpositive_sizes",
    "tests/unit/test_array.py::test_validate_grid_shape_rejects_nonpositive",
    "tests/unit/test_array.py::test_tile_cache_key_includes_height",
]


@dataclass(frozen=True)
class Mutant:
    id: str
    path: Path
    needle: str
    replacement: str
    tests: tuple[str, ...]
    note: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        id="F1",
        path=FETCH,
        needle="            if len(events) < self._forbidden_threshold:",
        replacement="            if len(events) <= self._forbidden_threshold:",
        tests=tuple(FETCH_TESTS),
        note="403 circuit breaker trips one 403 late",
    ),
    Mutant(
        id="F2",
        path=FETCH,
        needle=(
            "            cutoff = now - self._forbidden_window_seconds\n"
            "            while events and events[0] < cutoff:\n"
            "                events.popleft()\n"
        ),
        replacement="            cutoff = now - self._forbidden_window_seconds\n",
        tests=tuple(FETCH_TESTS),
        note="breaker window ignored (sparse 403s trip it)",
    ),
    Mutant(
        id="F3",
        path=FETCH,
        needle=(
            "            events.clear()\n"
            "            self._frozen_until[host] = now + self._forbidden_cooldown_seconds\n"
            "            self.circuit_breaker_trips += 1\n"
        ),
        replacement=(
            "            events.clear()\n            self.circuit_breaker_trips += 1\n"
        ),
        tests=tuple(FETCH_TESTS),
        note="breaker never freezes",
    ),
    Mutant(
        id="F4",
        path=FETCH,
        needle=(
            "    if isinstance(exc, RetryableHTTPError) and exc.retry_after is not None:\n"
            "        return max(exc.retry_after, 0.0)\n"
        ),
        replacement=(
            "    if isinstance(exc, RetryableHTTPError) and exc.retry_after is not None:\n"
            "        return 0.0\n"
        ),
        tests=tuple(FETCH_TESTS),
        note="Retry-After ignored",
    ),
    Mutant(
        id="F7",
        path=FETCH,
        needle="    return base * min(_MAX_DEADLINE_SCALE, max(1.0, scale))",
        replacement="    return base * max(1.0, scale)",
        tests=tuple(FETCH_TESTS),
        note="deadline scale uncapped",
    ),
    Mutant(
        id="F10",
        path=FETCH,
        needle=(
            "        state.limit = min(\n"
            "            self._maximum,\n"
            "            state.limit + self._additive_increase,\n"
            "        )\n"
        ),
        replacement=("        state.limit = state.limit + self._additive_increase\n"),
        tests=tuple(FETCH_TESTS),
        note="adaptive concurrency exceeds max_concurrent by one",
    ),
    Mutant(
        id="P2",
        path=PLANNING,
        needle=('        "width": request.width,\n        "height": request.height,\n'),
        replacement='        "width": request.width,\n',
        tests=tuple(PLANNING_TESTS),
        note="cache key ignores height",
    ),
    Mutant(
        id="P6",
        path=PLANNING,
        needle=(
            "def validate_grid_shape(grid: tuple[int, int]) -> tuple[int, int]:\n"
            "    rows, cols = grid\n"
            "    if rows <= 0 or cols <= 0:\n"
            '        raise ValidationError("grid_shape must contain positive integers")\n'
            "    return rows, cols\n"
        ),
        replacement=(
            "def validate_grid_shape(grid: tuple[int, int]) -> tuple[int, int]:\n"
            "    rows, cols = grid\n"
            "    if cols <= 0:\n"
            '        raise ValidationError("grid_shape must contain positive integers")\n'
            "    return rows, cols\n"
        ),
        tests=tuple(PLANNING_TESTS),
        note="grid_shape rows=0 accepted",
    ),
)


def _run_pytest(tests: tuple[str, ...]) -> int:
    cmd = [
        "uv",
        "run",
        "pytest",
        "-q",
        "--tb=line",
        "--timeout=30",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "-m",
        "not slow and not net",
        *tests,
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode


def _apply(mutant: Mutant) -> str:
    original = mutant.path.read_text(encoding="utf-8")
    if mutant.needle not in original:
        raise SystemExit(
            f"{mutant.id}: needle not found in {mutant.path.relative_to(ROOT)}"
        )
    if original.count(mutant.needle) != 1:
        raise SystemExit(
            f"{mutant.id}: needle not unique in {mutant.path.relative_to(ROOT)}"
        )
    mutant.path.write_text(
        original.replace(mutant.needle, mutant.replacement, 1), encoding="utf-8"
    )
    _PENDING_RESTORE.append((mutant.path, original))
    return original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated mutant ids (default: all)",
    )
    args = parser.parse_args(argv)
    selected = {part.strip().upper() for part in args.ids.split(",") if part.strip()}
    mutants = [m for m in MUTANTS if not selected or m.id in selected]
    if selected:
        missing = selected - {m.id for m in mutants}
        if missing:
            raise SystemExit(f"Unknown mutant ids: {sorted(missing)}")

    print(
        textwrap.dedent(
            f"""
            Planted mutation check ({len(mutants)} mutants)
            repo: {ROOT}
            """
        ).strip()
    )

    results: list[tuple[str, str, str]] = []
    for mutant in mutants:
        _apply(mutant)
        try:
            code = _run_pytest(mutant.tests)
        finally:
            _restore_pending()
        status = "killed" if code != 0 else "SURVIVED"
        results.append((mutant.id, status, mutant.note))
        print(f"  {mutant.id:4} {status:8}  {mutant.note}", flush=True)

    survived = [r for r in results if r[1] == "SURVIVED"]
    print()
    if survived:
        print(f"FAIL: {len(survived)} mutant(s) survived")
        return 1
    print(f"OK: all {len(results)} mutants killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
