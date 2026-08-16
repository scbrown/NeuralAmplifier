#!/usr/bin/env python3
"""Compare two runs' trajectories — the A/B half of na-6db that needs no game.

## What this is for

Every claim about decision quality so far is about legality, arithmetic and coherence. All are
necessary; none is the goal. **A brain that reasons beautifully into a worse position is the
failure mode this project most needs to detect, and it is invisible in a single decision
record.** Seeing it requires playing the same save forward twice — once with `llm_factions=0`,
once with the brain — and comparing what the game itself keeps score of.

Producing those two logs needs a running game. Reading them does not, which is why this exists
now: when someone has a game, the analysis is already written and already argues with them.

## The confound this refuses to let you have

na-6db names it and it is easy to walk into: an AI slot inherits difficulty handicaps a human
slot does not (invariant 6). **Comparing an LLM-driven AI slot against a native AI slot at a
different difficulty measures the handicap, not the brain** — and it produces a clean-looking
number with a confident sign.

So this REFUSES a comparison whose arms do not share a fairness profile, rather than reporting
one with a caveat. A caveat is something a reader can skip; a refusal is not. That is the whole
reason the adapter emits `fairness` on every record.

## What it reports

The game's own measures, taken from the `metrics` block every world view already carries —
base count, mineral surplus, energy, labs, military, drones. Per-arm trajectories and the
delta at the last shared turn.

It reports **no verdict**. There is no "the brain won" line, because a trajectory over one save
is evidence and not a result, and a script that announced a winner would be read as one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Metrics worth trending. Deliberately the ones the GAME keeps, not ones we invented: a
#: comparison on a measure of our own choosing is a comparison we can accidentally rig.
TRENDED = (
    "base_count",
    "pop_total",
    "mineral_surplus",
    "energy_reserves",
    "energy_income",
    "labs_output",
    "military_units",
    "drone_total",
)


def load_arm(path: Path) -> tuple[dict[int, dict[str, int]], dict, set[str]]:
    """(metrics by turn, fairness profile, tiers seen) for one run's observation log.

    Takes the LAST record for a turn rather than the first: a turn emits many records as bases
    are decided, and the end-of-turn state is the one that reflects what the turn actually did.
    """
    by_turn: dict[int, dict[str, int]] = {}
    fairness: dict = {}
    tiers: set[str] = set()

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        turn = record.get("turn")
        metrics = record.get("metrics")
        if isinstance(turn, int) and isinstance(metrics, dict):
            by_turn[turn] = {k: v for k, v in metrics.items() if isinstance(v, int)}
        if isinstance(record.get("fairness"), dict) and not fairness:
            fairness = record["fairness"]
        if isinstance(record.get("tier"), str):
            tiers.add(record["tier"])

    return by_turn, fairness, tiers


def profile_key(fairness: dict) -> tuple:
    """The part of a fairness block two arms must share.

    Slot, difficulty, and the STRUCTURAL handicaps — the ones nobody selected. A handicap
    somebody chose is a legitimate difference between arms; one the engine imposed is the
    confound.
    """
    handicaps = fairness.get("handicaps") or []
    structural = sorted(
        h.get("name", "")
        for h in handicaps
        if isinstance(h, dict) and h.get("selected_by") == "structural"
    )
    return (fairness.get("slot"), fairness.get("difficulty"), tuple(structural))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("baseline", help="observation log from the llm_factions=0 arm")
    ap.add_argument("brain", help="observation log from the brain arm")
    ap.add_argument(
        "--min-turns",
        type=int,
        default=30,
        help="refuse a comparison shorter than this (na-6db's acceptance says 30)",
    )
    ap.add_argument(
        "--allow-profile-mismatch",
        action="store_true",
        help="compare anyway. The result measures the handicap, not the brain.",
    )
    args = ap.parse_args()

    base_turns, base_fair, base_tiers = load_arm(Path(args.baseline))
    brain_turns, brain_fair, brain_tiers = load_arm(Path(args.brain))

    for name, turns in (("baseline", base_turns), ("brain", brain_turns)):
        if not turns:
            print(f"{name}: no records carrying both a turn and metrics", file=sys.stderr)
            return 1

    # THE REFUSAL. A mismatched profile produces a clean number with a confident sign that
    # answers a different question than the one asked.
    if profile_key(base_fair) != profile_key(brain_fair):
        print("refusing: the two arms do not share a fairness profile.", file=sys.stderr)
        print(f"  baseline  slot={base_fair.get('slot')} "
              f"difficulty={base_fair.get('difficulty')}", file=sys.stderr)
        print(f"  brain     slot={brain_fair.get('slot')} "
              f"difficulty={brain_fair.get('difficulty')}", file=sys.stderr)
        print(
            "\nAn AI slot inherits handicaps a human slot does not (invariant 6), so this\n"
            "comparison would measure the handicap and not the brain. Re-run both arms on\n"
            "the same slot and difficulty.",
            file=sys.stderr,
        )
        if not args.allow_profile_mismatch:
            return 1
        print("--allow-profile-mismatch given; continuing under protest.\n", file=sys.stderr)

    shared = sorted(set(base_turns) & set(brain_turns))
    if len(shared) < args.min_turns:
        print(
            f"refusing: only {len(shared)} shared turn(s), and a trajectory needs "
            f"{args.min_turns}. A short run measures the opening, not the decisions.",
            file=sys.stderr,
        )
        return 1

    # A baseline arm that ran the brain is not a baseline. Cheap to check, and it invalidates
    # everything downstream if missed.
    if "llm" in base_tiers:
        print(
            "refusing: the baseline arm contains llm-tier decisions, so it is not a baseline.",
            file=sys.stderr,
        )
        return 1
    if "llm" not in brain_tiers:
        print(
            "refusing: the brain arm contains no llm-tier decisions — both arms are the "
            "engine, and the comparison would report noise as a result.",
            file=sys.stderr,
        )
        return 1

    first, last = shared[0], shared[-1]
    print(f"turns {first}–{last} ({len(shared)} shared)")
    print(f"fairness: slot={base_fair.get('slot')} difficulty={base_fair.get('difficulty')} "
          f"structural handicaps={len(profile_key(base_fair)[2])}")
    print()
    print(f"{'metric':<20} {'baseline':>10} {'brain':>10} {'delta':>10}")
    for metric in TRENDED:
        b = base_turns[last].get(metric)
        m = brain_turns[last].get(metric)
        if b is None or m is None:
            continue
        print(f"{metric:<20} {b:>10} {m:>10} {m - b:>+10}")

    print(
        "\nNo verdict. One save's trajectory is evidence, not a result — run several saves\n"
        "before believing a sign, and read the decision records for why, not just the delta."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
