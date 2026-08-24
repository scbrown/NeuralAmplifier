#!/usr/bin/env python3
"""All-faction base census read straight out of a saved game. Never touches a running one.

WHY IT EXISTS. The all-faction census (``bases=1:32,2:51,...``) is normally obtained by asking
the live game through its ``na-command`` channel — the same single-slot channel
``drive-unattended.py`` polls, where ``SILENT_LIMIT`` consecutive unanswered polls make the
driver declare the game gone and stop. During a controlled A/B that is not an acceptable risk:
the arm's whole value is that nothing perturbed it. A save is written BY the game, sits on disk,
and reading it cannot reach the process at all.

THE FORMAT, stated so it is falsifiable rather than trusted:

* saves are uncompressed and begin with ``TERRAN``.
* bases live in one contiguous table of 308-byte records.
* the base NAME is a NUL-terminated string at the start of the record, and the OWNING FACTION is
  the byte 15 before it.
* a base's index in that table is its ``base_id`` — the same id the orchestrator reports.

None of that was read from a specification, so this module re-derives the table on every file
(longest run of 308-strided records that each carry a plausible name and a faction in 1..7) and
refuses rather than guessing. It was validated against a census produced by a completely
different mechanism — the engine's own observer, as published in ``evals/runs/na-clk`` — and
reproduces its turn-13 and turn-21 rows exactly, plus its turn-123 reading to within the two
turns of drift you would expect from reading turn 121.

THE TRAP IT IS BUILT AROUND. ``saves/auto`` is a namespace keyed by GAME YEAR, not by run.
``Autosave_2150.sav`` does not mean "turn 50 of this run", it means "turn 50 of whatever ran in
this directory last" — and a run directory routinely holds saves from rejected preflights and
from earlier games. Measured while building this: one directory held turn 1-13 of the accepted
run, turn 14-55 of an excluded one, and files from two months earlier, all under the same names.
So ``--since`` is REQUIRED, and a save older than it is refused rather than quietly censused.

CONVENTION. ``census(turn T) = Autosave_{2100+T}.sav``, which is what ``evals/runs/na-clk``'s
published table already uses; keeping one convention is what makes a new arm comparable to the
rows already published. That save is the START-of-turn state, so on the exact turn a base is
founded it reads one lower than the end-of-turn world view. Arms must not mix the two.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

#: Size of one base record. Derived, not documented — see the module docstring.
STRIDE = 0x134

#: Where the owning faction sits, relative to the start of the record's name.
FACTION_OFFSET = -15

MAGIC = b"TERRAN"

#: Saves are named for the game year, and year 2101 is turn 1.
YEAR_ZERO = 2100

FACTIONS = range(1, 8)


def name_at(data: bytes, pos: int) -> str | None:
    """The NUL-terminated base name starting at ``pos``, or None if that is not one."""
    end = data.find(b"\x00", pos, pos + 25)
    if end <= pos:
        return None
    text = data[pos:end]
    if len(text) < 2 or not all(32 <= byte < 127 for byte in text):
        return None
    return text.decode("latin1")


def record_offsets(data: bytes) -> list[int]:
    """The longest run of 308-strided base records in the file.

    Deliberately not "the table is at offset X": the table MOVES between saves of the same game,
    because what precedes it varies. Finding it by its own shape is what lets this run against a
    save nobody has seen before.
    """
    candidates = {
        pos
        for pos in range(-FACTION_OFFSET, len(data) - 26)
        if data[pos + FACTION_OFFSET] in FACTIONS and name_at(data, pos) is not None
    }
    best: list[int] = []
    for pos in sorted(candidates):
        if pos - STRIDE in candidates:
            continue  # not the head of a run
        run = []
        walk = pos
        while walk in candidates:
            run.append(walk)
            walk += STRIDE
        if len(run) > len(best):
            best = run
    return best


def census(path: Path) -> dict:
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError(f"{path} is not a saved game (it does not begin with {MAGIC!r})")
    offsets = record_offsets(data)
    if not offsets:
        raise ValueError(f"no base table found in {path}")
    bases = [(name_at(data, off), data[off + FACTION_OFFSET]) for off in offsets]
    counts = Counter(faction for _, faction in bases)
    return {
        "file": str(path),
        "turn": turn_of(path),
        "total_bases": len(bases),
        "bases": {str(f): counts.get(f, 0) for f in FACTIONS},
        "names": {str(f): [n for n, owner in bases if owner == f] for f in FACTIONS},
    }


def turn_of(path: Path) -> int | None:
    match = re.search(r"(\d{4})", path.name)
    return int(match.group(1)) - YEAR_ZERO if match else None


def saves_in(directory: Path) -> list[Path]:
    return sorted(directory.glob("Autosave_2*.sav"), key=lambda p: turn_of(p) or 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="+", help="save files, or a saves/auto directory with --series")
    ap.add_argument(
        "--since",
        type=float,
        required=True,
        help="epoch seconds of the run's start. A save older than this is REFUSED: saves/auto is "
             "keyed by game year and holds other runs' files under identical names.",
    )
    ap.add_argument("--faction", type=int, default=7, help="the faction --series reports on")
    ap.add_argument("--series", action="store_true",
                    help="walk a directory and report the faction's whole trajectory")
    ap.add_argument("--names", action="store_true", help="print each faction's base names")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths: list[Path] = []
    for raw in args.target:
        path = Path(raw)
        if path.is_dir():
            paths.extend(saves_in(path))
        elif path.is_file():
            paths.append(path)
        else:
            print(f"refusing: no such save or directory {path}", file=sys.stderr)
            return 2

    rows, stale = [], []
    for path in paths:
        if path.stat().st_mtime < args.since:
            stale.append(path.name)
            continue
        try:
            rows.append(census(path))
        except ValueError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 2

    if stale:
        print(f"refused {len(stale)} save(s) as belonging to an earlier run (mtime predates "
              f"--since): {', '.join(stale[:6])}{' ...' if len(stale) > 6 else ''}",
              file=sys.stderr)
    if not rows:
        print("refusing: no save in this run's window — nothing to census", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rows, sort_keys=True))
        return 0

    for row in rows:
        turn = "?" if row["turn"] is None else row["turn"]
        print(f"turn {turn:>4}  {row['total_bases']:>4} bases  "
              + "  ".join(f"{f}:{row['bases'][f]}" for f in sorted(row["bases"], key=int)))
        if args.names:
            for faction in sorted(row["names"], key=int):
                if row["names"][faction]:
                    print(f"    faction {faction}: {', '.join(row['names'][faction])}")

    if args.series:
        return report_series(rows, args.faction)
    return 0


def report_series(rows: list[dict], faction: int) -> int:
    """Did this faction ever LOSE a base?

    Asked from the OUTCOME side on purpose. Whether a base was abandoned, captured or starved is
    three different mechanisms with one observable, and a config difference between two arms can
    reach any of them. A count that never falls rules out all three at once; a count that falls
    identifies none of them, and says so.
    """
    key = str(faction)
    series = [(row["turn"], row["bases"][key]) for row in rows]
    print(f"\nfaction {faction}: " + " ".join(f"{t}:{v}" for t, v in series))
    drops = [series[i] for i in range(1, len(series)) if series[i][1] < series[i - 1][1]]
    if drops:
        print(f"  faction {faction} DECREASED at turn(s) {[t for t, _ in drops]} — a base was "
              "abandoned, captured or starved. Which of the three this is cannot be read from "
              "the count; do not compare arms across it until it is explained.")
        return 1
    print(f"  faction {faction} never decreased: no base was abandoned, captured or starved in "
          "this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
