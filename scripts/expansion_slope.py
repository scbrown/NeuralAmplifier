#!/usr/bin/env python3
"""Compare two ladder rows' base-count slopes at shared census checkpoints (na-ff7).

Endpoints cannot answer whether expansion compounds. This reads the committed ``census`` tables
from two rows for one seed, refuses fairness or checkpoint mismatches, and prints each interval's
base gain and bases/turn. It deliberately prints no verdict: one seed is evidence, not a result.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab_outcomes import profile_key  # noqa: E402


def rows(path: Path) -> dict[tuple[int, str], dict]:
    found: dict[tuple[int, str], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        found[(int(row["seed"]), str(row["arm"]))] = row
    return found


def series(row: dict, faction: int, checkpoints: list[int]) -> list[tuple[int, int]]:
    census = row.get("census") or {}
    out: list[tuple[int, int]] = []
    for turn in checkpoints:
        snapshot = census.get(str(turn), census.get(turn))
        if not isinstance(snapshot, dict):
            raise ValueError(f"arm {row.get('arm')} has no numeric census at turn {turn}")
        value = snapshot.get(str(faction), snapshot.get(faction))
        if not isinstance(value, int):
            raise ValueError(f"arm {row.get('arm')} has no faction {faction} count at turn {turn}")
        out.append((turn, value))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--baseline", required=True, help="baseline arm name")
    ap.add_argument("--compound", required=True, help="compound arm name")
    ap.add_argument("--faction", type=int, required=True)
    ap.add_argument("--checkpoints", required=True, help="comma-separated shared turns")
    args = ap.parse_args()

    checkpoints = [int(value) for value in args.checkpoints.split(",")]
    if len(checkpoints) < 2 or checkpoints != sorted(set(checkpoints)):
        print(
            "refusing: checkpoints must be at least two unique turns in ascending order",
            file=sys.stderr,
        )
        return 1

    found = rows(Path(args.results))
    try:
        baseline = found[(args.seed, args.baseline)]
        compound = found[(args.seed, args.compound)]
    except KeyError as exc:
        print(f"refusing: missing seed/arm row {exc.args[0]}", file=sys.stderr)
        return 1
    if profile_key(baseline.get("fairness") or {}) != profile_key(compound.get("fairness") or {}):
        print("refusing: arms have different fairness profiles", file=sys.stderr)
        return 1
    try:
        arms = {
            args.baseline: series(baseline, args.faction, checkpoints),
            args.compound: series(compound, args.faction, checkpoints),
        }
    except ValueError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1

    print(f"seed {args.seed}, faction {args.faction}")
    fair = baseline.get("fairness") or {}
    print(f"fairness: slot={fair.get('slot')} difficulty={fair.get('difficulty')}")
    print(f"{'arm':<24} {'interval':<12} {'bases':>9} {'gain':>6} {'bases/turn':>11}")
    for arm, points in arms.items():
        for (start_turn, start), (end_turn, end) in pairwise(points):
            print(
                f"{arm:<24} {start_turn:>3}-{end_turn:<7} {start:>3}->{end:<3} "
                f"{end-start:>+6} {(end-start)/(end_turn-start_turn):>11.3f}"
            )
    print("\nNo verdict. One seed's slope is evidence, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
