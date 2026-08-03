#!/usr/bin/env python3
"""Copy the richest captured world view per surface into the committed fixtures.

**This exists because the manual version failed twice.** `faction.tech` and `base.hurry` were
both harvested to a `NA_WORLD_VIEW_STORE` under /tmp during na-b4v and na-1wu, and both times
only the surface the session happened to care about was copied out. /tmp was cleared. The
captures were real, they were expensive — they need a Wine game running to a suitable turn — and
they are gone (na-ibh).

The failure was never a lack of care. It is that the harvest was a judgement call made at the end
of a long session, about surfaces the session was not working on. So this takes the judgement
away: it copies EVERY surface present, and it says what it did.

    scripts/harvest-world-views.py /tmp/wv                  # what would change
    scripts/harvest-world-views.py /tmp/wv --write          # do it

Richest = largest serialised payload for that surface, which is a proxy for "most fields
populated" rather than for quality. It is the same rule the fixtures README already states, made
executable: these are not golden files, so a newer capture may replace an older one outright.

It deliberately does NOT delete a fixture whose surface is absent from the store. A run that
never reached `base.hurry` says nothing about whether the existing `base.hurry` capture is good,
and a harvest that pruned on silence would destroy exactly the rare captures this is protecting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "orchestrator" / "tests" / "fixtures" / "captured"


def load(store: Path) -> dict[str, tuple[Path, dict, int]]:
    """Richest capture per surface: {surface_id: (path, payload, size)}."""
    best: dict[str, tuple[Path, dict, int]] = {}
    for path in sorted(store.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A partially written capture is expected — the store writes as the game runs, and a
            # run killed mid-write costs one file. Skipping is right; failing the harvest for it
            # would throw away every other surface in the store.
            continue
        if not isinstance(payload, dict):
            continue
        surface = payload.get("surface_id")
        if not isinstance(surface, str) or not surface:
            continue
        size = len(json.dumps(payload))
        current = best.get(surface)
        if current is None or size > current[2]:
            best[surface] = (path, payload, size)
    return best


def fixture_name(surface: str, payload: dict) -> str:
    """`base.production` at turn 42 -> `base_production_turn42.json`, matching what is committed."""
    turn = payload.get("turn")
    stem = surface.replace(".", "_")
    return f"{stem}_turn{turn}.json" if isinstance(turn, int) else f"{stem}.json"


def existing_for(surface: str) -> list[Path]:
    stem = surface.replace(".", "_")
    return sorted(p for p in FIXTURES.glob(f"{stem}_turn*.json")) + sorted(
        FIXTURES.glob(f"{stem}.json")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("store", type=Path, help="a NA_WORLD_VIEW_STORE directory")
    ap.add_argument("--write", action="store_true", help="actually copy (default: dry run)")
    ap.add_argument(
        "--replace",
        action="store_true",
        help="remove the older fixture for a surface when adding a newer turn's capture",
    )
    args = ap.parse_args()

    if not args.store.is_dir():
        print(f"no such store: {args.store}", file=sys.stderr)
        return 2

    best = load(args.store)
    if not best:
        print(f"no world views in {args.store}")
        return 1

    FIXTURES.mkdir(parents=True, exist_ok=True)
    print(f"{'surface':<24}{'captures':>9}  {'action':<10} fixture")
    wrote = 0
    for surface in sorted(best):
        _, payload, size = best[surface]
        name = fixture_name(surface, payload)
        target = FIXTURES / name
        prior = [p for p in existing_for(surface) if p != target]
        action = "up-to-date" if target.exists() else ("replace" if prior else "NEW")
        print(f"{surface:<24}{size:>9}  {action:<10} {name}")
        for p in prior:
            print(f"{'':<24}{'':>9}  {'':<10}   (existing: {p.name})")
        if not args.write or target.exists():
            continue
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        wrote += 1
        if args.replace:
            for p in prior:
                p.unlink()
                print(f"{'':<24}{'':>9}  removed     {p.name}")

    # Surfaces we hold a fixture for that this run never produced. Reported, never pruned: a run
    # that did not reach a surface says nothing about the capture we already have, and these are
    # the expensive ones.
    held = {p.name.split("_turn")[0].replace("_", ".", 1) for p in FIXTURES.glob("*_turn*.json")}
    missing = sorted(held - set(best))
    if missing:
        print(f"\nnot in this run (fixtures kept): {', '.join(missing)}")

    if not args.write:
        print("\ndry run — pass --write to copy")
    else:
        print(f"\nwrote {wrote} fixture(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
