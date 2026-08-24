#!/usr/bin/env python3
"""Build one ``expansion_slope.py`` results row from a run's own saves and manifest.

This is the join between ``save_census.py`` (what the saves say) and ``expansion_slope.py``
(what the comparison needs), and it exists so that no census number is ever typed by hand
between the two. Hand-transcription is not a hypothetical risk here: the numbers being carried
across are single digits, six per arm, and a transposed one produces a slope that is wrong,
plausible, and unfalsifiable after the run directory is cleaned up.

It REFUSES to emit a partial row. `expansion_slope.py` already refuses a missing checkpoint;
emitting a row with a hole would simply move that refusal one step later, to a point where the
obvious repair is to fill the hole in by hand from a neighbouring turn. The refusal belongs
here, where the saves are still on disk and the honest fix is to wait for the turn.

Provenance travels with the numbers — the run root, the boundary used to reject other runs'
saves, and the exact save file each count came from — because a census is only checkable if a
reader can find the bytes it came from.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_census import census, saves_in, turn_of  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", help="run root holding manifest.json and play/saves/auto")
    ap.add_argument("--checkpoints", help="comma-separated turns; default: the manifest's")
    ap.add_argument("--since", type=float,
                    help="epoch boundary; default: the manifest's run_started_epoch")
    args = ap.parse_args()

    root = Path(args.run)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        print(f"refusing: no manifest at {manifest_path} — a row without a recorded arm, seed "
              "and fairness profile is not comparable to anything", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    since = args.since if args.since is not None else manifest.get("run_started_epoch")
    if since is None:
        print("refusing: no --since and no run_started_epoch in the manifest. saves/auto is "
              "keyed by game year, not by run, so without a boundary this row could be built "
              "from a different game's saves under identical names.", file=sys.stderr)
        return 2

    checkpoints = (
        [int(v) for v in args.checkpoints.split(",")]
        if args.checkpoints
        else list(manifest.get("census_turns") or [])
    )
    if len(checkpoints) < 2 or checkpoints != sorted(set(checkpoints)):
        print("refusing: need at least two unique checkpoints in ascending order",
              file=sys.stderr)
        return 2

    wanted = set(checkpoints)
    found: dict[int, dict] = {}
    for path in saves_in(root / "play" / "saves" / "auto"):
        turn = turn_of(path)
        if turn not in wanted or path.stat().st_mtime < float(since):
            continue
        found[turn] = census(path)

    missing = [t for t in checkpoints if t not in found]
    if missing:
        print(f"refusing: no in-run save for turn(s) {missing}. Not back-filled from a "
              "neighbouring turn: a census is a reading at a turn, and a row that quietly "
              "substitutes turn 49 for turn 50 defeats expansion_slope's own refusal.",
              file=sys.stderr)
        return 1

    row = {
        "seed": manifest.get("seed"),
        "arm": manifest.get("arm"),
        "fairness": manifest.get("fairness") or {},
        "our_faction": manifest.get("faction"),
        "census": {str(t): found[t]["bases"] for t in checkpoints},
        "provenance": {
            "run": str(root),
            "since": float(since),
            "saves": {str(t): Path(found[t]["file"]).name for t in checkpoints},
            "census_convention": "census(turn T) = Autosave_{2100+T}.sav (start-of-turn state)",
        },
    }
    print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
