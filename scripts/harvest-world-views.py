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
    scripts/harvest-world-views.py /tmp/wv --log "$PLAY_DIR"   # both sinks

**Two sinks, and reading one of them was this script's own version of the bug it exists to
prevent (na-0oa).** A world view can land in the `NA_WORLD_VIEW_STORE` directory *or* in the
adapter's own `na-observations.jsonl`, which lives in the SMAC install rather than in this repo
or in /tmp. `base.hurry` sat in a play directory's log from 2026-08-02 while two separate sweeps
concluded no capture survived anywhere on disk — both searched the repo and /tmp, and the log is
in neither. That is exactly the failure this script was built to stop, arriving through the door
it did not watch, and it was worse than an ordinary gap because the script's existence is what
makes people stop looking by hand.

So it reads both, and it names the sink each capture came from: "the store had it" and "only the
log had it" are different facts about a run and must stay distinguishable. `--log` takes either
the file or the play directory that contains it, repeatably, and `SMAC_PLAY_DIR` is checked
automatically so the common case needs no flag. The store argument is optional — a session that
only has the adapter log is exactly the case this was blind to.

The log is one file appended across every run of the game, mixing full world views with the
compact divergence records `na_verify_*` emits, so a line counts as a capture only if it carries
a non-empty `surface_id` and all four of the contract's required fields. A divergence record has
a `surface_id` and none of the rest; without the check it would be picked for any surface it was
the only line for, and written out as a fixture that never was a world view.

Richest = largest serialised payload for that surface, which is a proxy for "most fields
populated" rather than for quality. It is the same rule the fixtures README already states, made
executable: these are not golden files, so a newer capture may replace an older one outright. The
rule spans both sinks rather than preferring either — where a surface reached both, the fuller
capture is the one worth committing regardless of which door it came through.

It deliberately does NOT delete a fixture whose surface is absent from the sources. A run that
never reached `base.hurry` says nothing about whether the existing `base.hurry` capture is good,
and a harvest that pruned on silence would destroy exactly the rare captures this is protecting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "orchestrator" / "tests" / "fixtures" / "captured"

#: The adapter's own log, by the name it has in every play directory.
LOG_NAME = "na-observations.jsonl"

#: `WorldView`'s required fields (`contract.py`). Checked structurally rather than by importing
#: the model: a capture whose schema has drifted since it was taken is still the only copy of an
#: expensive surface, and refusing to harvest it would destroy it to enforce a schema.
REQUIRED = ("engine", "scope", "turn", "faction")


def _capture(payload: object) -> tuple[str, dict, int] | None:
    """(surface, payload, size) if this is a world view, else None."""
    if not isinstance(payload, dict):
        return None
    surface = payload.get("surface_id")
    if not isinstance(surface, str) or not surface:
        return None
    if any(key not in payload for key in REQUIRED):
        return None
    return surface, payload, len(json.dumps(payload))


def from_store(store: Path) -> list[tuple[str, dict, int, str]]:
    """Every world view in a `NA_WORLD_VIEW_STORE` directory, tagged with its origin."""
    out: list[tuple[str, dict, int, str]] = []
    for path in sorted(store.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A partially written capture is expected — the store writes as the game runs, and a
            # run killed mid-write costs one file. Skipping is right; failing the harvest for it
            # would throw away every other surface in the store.
            continue
        found = _capture(payload)
        if found is not None:
            out.append((*found, "store"))
    return out


def from_log(path: Path, label: str = "log") -> list[tuple[str, dict, int, str]]:
    """Every world view in an adapter `na-observations.jsonl`, tagged with its origin.

    ``label`` is what the report prints in the origin column. It stays short — the full paths
    are named once in the header, and a column wide enough for a play directory would push the
    thing anyone is actually reading off the right of the terminal.

    A truncated final line is expected rather than exceptional: the log is appended as the game
    runs and a run killed mid-write leaves one. Skipping it costs that line; raising would cost
    every capture in the file, which is the tradeoff this script exists to refuse.
    """
    out: list[tuple[str, dict, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = _capture(payload)
        if found is not None:
            out.append((*found, label))
    return out


def resolve_log(where: Path) -> Path | None:
    """Accept the file or the play directory that holds it — nobody remembers which."""
    if where.is_dir():
        candidate = where / LOG_NAME
        return candidate if candidate.is_file() else None
    return where if where.is_file() else None


def richest(candidates: list[tuple[str, dict, int, str]]) -> dict[str, tuple[dict, int, str]]:
    """Best capture per surface across every source: {surface_id: (payload, size, origin)}."""
    best: dict[str, tuple[dict, int, str]] = {}
    for surface, payload, size, origin in candidates:
        current = best.get(surface)
        if current is None or size > current[1]:
            best[surface] = (payload, size, origin)
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
    ap.add_argument(
        "store",
        type=Path,
        nargs="?",
        help="a NA_WORLD_VIEW_STORE directory (optional — --log alone is a valid harvest)",
    )
    ap.add_argument(
        "--log",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help=f"an adapter {LOG_NAME}, or the play directory holding one. Repeatable. "
        "SMAC_PLAY_DIR is checked automatically.",
    )
    ap.add_argument("--write", action="store_true", help="actually copy (default: dry run)")
    ap.add_argument(
        "--replace",
        action="store_true",
        help="remove the older fixture for a surface when adding a newer turn's capture",
    )
    args = ap.parse_args()

    candidates: list[tuple[str, dict, int, str]] = []
    sources: list[str] = []

    if args.store is not None:
        if not args.store.is_dir():
            print(f"no such store: {args.store}", file=sys.stderr)
            return 2
        candidates += from_store(args.store)
        sources.append(f"store {args.store}")

    # Named logs first, then the environment's — so an explicit path wins the dedupe and the
    # output names what the operator asked for rather than what was inferred.
    wanted = list(args.log)
    play_dir = os.environ.get("SMAC_PLAY_DIR")
    if play_dir:
        wanted.append(Path(play_dir))

    seen: set[Path] = set()
    for where in wanted:
        resolved = resolve_log(where)
        if resolved is None:
            # Not fatal. A missing log is the ordinary state of a machine with no game on it,
            # and failing here would make the store-only harvest impossible to run from a repo
            # checkout — which is where most of them happen.
            print(f"no adapter log at {where}", file=sys.stderr)
            continue
        resolved = resolved.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        # Numbered only when there is more than one, so the ordinary single-log run reads as a
        # plain "log" and the number means something when it appears.
        label = "log" if len(wanted) == 1 else f"log[{len(seen)}]"
        candidates += from_log(resolved, label)
        sources.append(f"{label} {resolved}")

    if not sources:
        print("nothing to harvest: name a store, a --log, or set SMAC_PLAY_DIR", file=sys.stderr)
        return 2

    best = richest(candidates)
    print("reading: " + ", ".join(sources))
    if not best:
        print("no world views found")
        return 1

    FIXTURES.mkdir(parents=True, exist_ok=True)
    print(f"\n{'surface':<24}{'captures':>9}  {'origin':<12} {'action':<10} fixture")
    wrote = 0
    for surface in sorted(best):
        payload, size, origin = best[surface]
        name = fixture_name(surface, payload)
        target = FIXTURES / name
        prior = [p for p in existing_for(surface) if p != target]
        action = "up-to-date" if target.exists() else ("replace" if prior else "NEW")
        print(f"{surface:<24}{size:>9}  {origin:<12} {action:<10} {name}")
        for p in prior:
            print(f"{'':<24}{'':>9}  {'':<12} {'':<10}   (existing: {p.name})")
        if not args.write or target.exists():
            continue
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        wrote += 1
        if args.replace:
            for p in prior:
                p.unlink()
                print(f"{'':<24}{'':>9}  {'':<12} removed     {p.name}")

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
