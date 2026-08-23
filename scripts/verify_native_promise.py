#!/usr/bin/env python3
"""Prove the two native-tier features against a LIVE game (na-4lr action space, na-nmg withdrawal).

    scripts/verify_native_promise.py <play-dir> [--target -1] [--turns 6]

Both features were committed compiling but unverified at run time, and both are the kind that
pass a weak check while doing nothing:

  * `dialog-options` can return an action space that is REAL but WRONG — `#ENDOFTURN`'s two lines
    of prose read exactly like two buttons, and a parser that offers them to the brain has
    invented a choice. So this asserts the counts for three labels chosen because they disagree:
    a real two-button dialog, a notice with a blank line and no buttons, and the prose trap.

  * withdrawal enforcement passes a weak acceptance trivially. `units_in_foreign_territory`
    counts POSITION, so installing the avoidance bias changes NOTHING about a unit already
    standing on somebody's land — and every surface would report the directive as served. The
    acceptance sattler ruled, and this bead's own rule, is the METRIC FALLING turn over turn.
    So this reads the metric before, issues the promise, plays turns, and reads it again.

Exit 0 only if every arm passed. Prints what it measured either way — a run that could not test
something says so rather than counting it as a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

#: (label, expected option_count, why this label is in the set)
DIALOG_CASES = [
    ("GOVCITIZENS", 2, "a real two-button dialog: prose, blank, two options"),
    ("PSYCHREQUEST", 0, "a notice: prose, blank, then the NEXT label — no buttons"),
    ("ENDOFTURN", 0, "THE TRAP: two lines of prose that read exactly like two buttons"),
]


def cmd(play: Path, line: str, wait: float = 25.0) -> dict | None:
    """One command down the adapter's channel. `None` means it never answered."""
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


def turn_of(play: Path) -> int | None:
    r = cmd(play, "game-state")
    return r.get("turn") if r else None


def foreign_units(worldviews: Path) -> int | None:
    """The metric, read from the newest WORLD VIEW — the number the brain actually sees.

    Deliberately not a second implementation, and deliberately not a new adapter command either.
    A `metrics` command would be a third place this number is computed, and the failure that
    matters here is enforcement and the metric disagreeing about which tiles count — adding
    another reader would make that harder to see, not easier.
    """
    files = sorted(worldviews.glob("*.json"), key=lambda f: f.stat().st_mtime)
    for path in reversed(files[-40:]):
        try:
            view = json.loads(path.read_text())
        except Exception:
            continue
        metrics = view.get("metrics")
        if isinstance(metrics, dict) and "units_in_foreign_territory" in metrics:
            value = metrics["units_in_foreign_territory"]
            return value.get("value") if isinstance(value, dict) else value
    return None


def check_dialogs(play: Path) -> list[tuple[bool, str]]:
    out = []
    for label, expect, why in DIALOG_CASES:
        r = cmd(play, f"dialog-options {label}")
        if not r or not r.get("ok"):
            out.append((False, f"dialog-options {label}: no answer — {why}"))
            continue
        try:
            body = json.loads(r.get("detail") or "{}")
        except Exception:
            out.append((False, f"dialog-options {label}: detail was not JSON: {r.get('detail')!r}"))
            continue
        got = body.get("option_count")
        ok = got == expect
        out.append((ok, f"dialog-options {label}: option_count={got} expected={expect} — {why}"))
    return out


def check_withdrawal(play: Path, worldviews: Path, target: int, turns: int) -> list[tuple[bool, str]]:
    out = []
    before = foreign_units(worldviews)
    if before is None:
        return [(False, "could not read units_in_foreign_territory — nothing below is testable")]
    out.append((True, f"units_in_foreign_territory before = {before}"))
    if before == 0:
        # NOT a pass. A metric already at zero cannot be seen to fall, and calling that success
        # is exactly how an inert enforcement ships.
        out.append((False, "metric is already 0: this run cannot demonstrate the promise being "
                           "kept. Re-run when units are standing on foreign land."))
        return out

    r = cmd(play, f"withdraw {target} 0")
    out.append((bool(r and r.get("ok")), f"withdraw {target} 0 -> {r.get('detail') if r else 'no answer'}"))

    start = turn_of(play)
    end_by = time.time() + 60 * turns
    while time.time() < end_by:
        now = turn_of(play)
        if now is not None and start is not None and now >= start + turns:
            break
        time.sleep(5)

    stats = cmd(play, "withdraw-stats")
    out.append((True, f"withdraw-stats: {stats.get('detail') if stats else 'no answer'}"))
    after = foreign_units(worldviews)
    out.append((
        after is not None and after < before,
        f"units_in_foreign_territory after {turns} turns = {after} (was {before}) — "
        "the acceptance is this number FALLING, not the bias being installed",
    ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("play_dir")
    ap.add_argument("--target", type=int, default=-1, help="-1 = all foreign territory")
    ap.add_argument("--turns", type=int, default=6)
    ap.add_argument("--worldviews", default="", help="orchestrator worldview dir (holds the metric)")
    ap.add_argument("--skip-withdrawal", action="store_true")
    args = ap.parse_args()

    play = Path(args.play_dir)
    if not play.is_dir():
        print(f"no such play dir: {play}", file=sys.stderr)
        return 2
    if turn_of(play) is None:
        print("the game is not answering the command channel — nothing here is testable",
              file=sys.stderr)
        return 2

    results = check_dialogs(play)
    if not args.skip_withdrawal:
        wv = Path(args.worldviews) if args.worldviews else play.parent / "orch" / "worldviews"
        if not wv.is_dir():
            results.append((False, f"no worldview dir at {wv} — the metric cannot be read, so the "
                                   "withdrawal arm is UNTESTED rather than passed"))
        else:
            results += check_withdrawal(play, wv, args.target, args.turns)

    print()
    for ok, line in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {line}")
    failed = [line for ok, line in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
