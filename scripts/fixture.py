#!/usr/bin/env python3
"""Named SAVE STATES as test fixtures — capture one, load it as many times as you like.

    scripts/fixture.py capture <name> --play-dir DIR [--note "..."]
    scripts/fixture.py list
    scripts/fixture.py show <name>

Stiwi, 2026-08-23: *"the game supports save states. you should be able to load up specific save
states to test specific things. evals can work in this same way."*

## Why this is not a convenience

Every na-1lj hypothesis so far cost a fresh 15-minute run: launch, play to turn 15-25, wait for a
colony pod to exist AND freeze, then measure. Nine hypotheses, nine runs, and each one landed on
a DIFFERENT map position with different neighbours — so two runs were never the same experiment,
which is exactly the confound that made `settle-mode` and `unit-turn` need an in-game A/B switch
to be measurable at all.

A save state removes both problems at once. The state under test is captured once and addressed
by name; loading it takes about a minute and gives every arm a byte-identical starting point. The
comparison stops being "two runs that both had a stuck pod somewhere" and becomes "the same pod,
on the same tile, with the same neighbours, under two builds".

na-6db already proved the shape — two arms from one byte-identical save, md5 verified. This makes
it the default method instead of a thing that was done once by hand.

## What a fixture is

A `.sav` plus a `.json` manifest recording the turn, the md5, and — when the game was live at
capture — the `game-state` and `move-stats` readings that made the state interesting. The
manifest is the point: a save with no record of WHY it was kept is a file nobody dares delete and
nobody can use.

## Loading one

    NA_SAVE=evals/fixtures/<name>.sav scripts/play-thinker.sh headless

The launcher copies it to `saves/fixture/loaded.sav` and autoloads it by that path, rather than
resuming "whatever is newest by mtime" — which is right for continuing a run and wrong for an
experiment, because it makes the thing under test depend on what else wrote to the directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "evals" / "fixtures"


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ask(play: Path, line: str, wait: float = 20.0) -> dict | None:
    """One adapter command. `None` means the game did not answer — not that it said no."""
    result = play / "na-command-result"
    result.unlink(missing_ok=True)
    (play / "na-command").write_text(line)
    end = time.time() + wait
    while time.time() < end:
        if result.exists():
            try:
                return json.loads(result.read_text())
            except Exception:
                pass
        time.sleep(0.4)
    return None


def cmd_capture(args: argparse.Namespace) -> int:
    play = Path(args.play_dir)
    auto = play / "saves" / "auto"
    saves = sorted(auto.glob("*.sav"), key=lambda f: f.stat().st_mtime)
    if not saves:
        print(f"no autosaves in {auto}", file=sys.stderr)
        return 1
    newest = saves[-1]

    FIXTURES.mkdir(parents=True, exist_ok=True)
    dest = FIXTURES / f"{args.name}.sav"
    if dest.exists() and not args.force:
        print(f"refusing: {dest} exists. A fixture is a fixed point — overwriting one silently "
              f"invalidates every result that cites it. Use --force if you mean it.",
              file=sys.stderr)
        return 1
    shutil.copy2(newest, dest)

    # The readings that make the state worth keeping, IF the game is still live. Recorded rather
    # than described: "the pod was stuck" is a memory, `pod=136,84 wp=125,83` is a fixture.
    live: dict = {}
    if (play / "na-command").parent.is_dir():
        for probe in ("game-state", "move-stats", "build-stats"):
            answer = ask(play, probe)
            if answer and answer.get("detail"):
                live[probe] = answer["detail"]

    manifest = {
        "name": args.name,
        "source": str(newest.name),
        "md5": md5(dest),
        "bytes": dest.stat().st_size,
        "captured": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": args.note,
        "readings": live,
    }
    (FIXTURES / f"{args.name}.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"captured {dest.name}  md5 {manifest['md5'][:12]}  from {newest.name}")
    for key, value in live.items():
        print(f"  {key}: {value}")
    print(f"\nload it with:\n  NA_SAVE={dest.relative_to(REPO)} scripts/play-thinker.sh headless")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if not FIXTURES.is_dir():
        print("no fixtures captured yet")
        return 0
    for manifest_path in sorted(FIXTURES.glob("*.json")):
        m = json.loads(manifest_path.read_text())
        sav = FIXTURES / f"{m['name']}.sav"
        state = "" if sav.is_file() else "  [MISSING .sav]"
        print(f"{m['name']:<28} {m['md5'][:12]}  {m.get('captured', '')}{state}")
        if m.get("note"):
            print(f"    {m['note']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    path = FIXTURES / f"{args.name}.json"
    if not path.is_file():
        print(f"no such fixture: {args.name}", file=sys.stderr)
        return 1
    m = json.loads(path.read_text())
    print(json.dumps(m, indent=2, sort_keys=True))
    sav = FIXTURES / f"{m['name']}.sav"
    if sav.is_file():
        actual = md5(sav)
        # A fixture whose bytes changed is not the fixture any result cited.
        print(f"\nmd5 on disk: {actual}  {'OK' if actual == m['md5'] else 'MISMATCH — the save has changed'}")
    else:
        print("\nthe .sav is MISSING — nothing can be reproduced from this manifest")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="keep the current state as a named fixture")
    cap.add_argument("name")
    cap.add_argument("--play-dir", required=True)
    cap.add_argument("--note", default="")
    cap.add_argument("--force", action="store_true")
    cap.set_defaults(func=cmd_capture)

    lst = sub.add_parser("list", help="what fixtures exist")
    lst.set_defaults(func=cmd_list)

    shw = sub.add_parser("show", help="one fixture's manifest, and whether its bytes still match")
    shw.add_argument("name")
    shw.set_defaults(func=cmd_show)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
