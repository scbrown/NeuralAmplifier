#!/usr/bin/env python3
"""The win ladder — N seeded games, LLM slot vs 6 CPU, win/loss recorded per seed (na-clk).

    scripts/win_ladder.py run    --seeds 1,2,3 --out evals/runs/na-clk/results.jsonl [...]
    scripts/win_ladder.py report evals/runs/na-clk/results.jsonl

This is the instrument for na-xb1's goal — "the LLM-driven faction wins a standard game against
6 stock CPU factions, 2 of 3 seeded games". Everything below exists to make that number
un-arguable rather than to produce it quickly.

## Why a ladder and not another A/B

`ab-outcomes` compares TRAJECTORIES: base count, energy, research at a shared turn. That is what
M1 and M2 measured and it is the right tool for "is the brain playing better". It cannot answer
"does the brain WIN", because a victory is a discrete event at an unknown turn and a trajectory
metric is not a proxy for it. A faction can lead on every metric at turn 140 and lose at 210.

## The refusals, and why each one exists

Every one of these has already cost this project something, on this bead's siblings:

**A fairness profile that differs between seeds.** Imported from `ab_outcomes.profile_key` rather
than reimplemented — the same key, so the two tools cannot drift into disagreeing about what a
comparable run is. An AI slot inherits difficulty handicaps a human slot does not (AGENTS.md
invariant 6), and a ladder mixing slots measures the handicap.

**A run that ended without a game-over.** `halted` and `STATE_GAME_DONE` are different facts: a
process that exited, a run that hit its turn limit, and a game that was WON look identical from
outside unless something asks. A seed with no `game-state` reading is recorded as `unresolved`,
never as a loss — an unfinished game is not a defeat, and scoring it as one is how a ladder
flatters whichever side happened to time out.

**A seed reused across configurations.** The results file keys on (seed, arm), so re-running one
arm cannot silently overwrite the other's row.

## What it does NOT do

It does not decide what counts as a win. `game-state` reports the engine's own `GameState` bits
and every faction's base count; `report` turns those into win/loss/unresolved by an explicit rule
written here, where it can be argued with, rather than inside the adapter.

It does not start a seeded game. That needs an engine seam this fork does not have yet — see
`docs/headless-harness.md` and the na-clk bead. `run` takes prepared per-seed saves and will
refuse rather than pretend a shared save is a seeded ladder.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab_outcomes import profile_key  # noqa: E402  — one definition of a comparable run

REPO = Path(__file__).resolve().parents[1]

#: Bits from the engine's `GameState` enum, as `game-state` reports them.
VICTORY_KINDS = ("conquest", "diplomatic", "economic")


def read_state(play_dir: Path, timeout: float = 20.0) -> dict | None:
    """Ask the running game who won. `None` means it did not answer — not that nobody won."""
    result = play_dir / "na-command-result"
    result.unlink(missing_ok=True)
    (play_dir / "na-command").write_text("game-state")
    end = time.time() + timeout
    while time.time() < end:
        if result.exists():
            try:
                return json.loads(result.read_text())
            except Exception:
                pass
        time.sleep(0.4)
    return None


def parse_state(detail: str) -> dict:
    """`state=0x110002 done=0 ... victory=none bases=1:48,2:50,...` into a dict.

    Parsed rather than eval'd, and unknown keys are kept: the adapter may add a field and a
    harness that drops what it does not recognise is one that silently stops reporting it.
    """
    out: dict = {"bases": {}}
    for token in detail.split():
        key, _, value = token.partition("=")
        if key == "bases":
            for pair in value.split(","):
                fid, _, count = pair.partition(":")
                if fid.isdigit() and count.isdigit():
                    out["bases"][int(fid)] = int(count)
        elif value.startswith("0x"):
            out[key] = int(value, 16)
        elif value.lstrip("-").isdigit():
            out[key] = int(value)
        else:
            out[key] = value
    return out


def verdict(state: dict, our_faction: int) -> tuple[str, str]:
    """(outcome, why) for one finished seed.

    The rule is HERE, in the harness, not in the adapter — so it can be argued with. Three
    outcomes and not two: `unresolved` is a real answer and the most important one to keep
    separate, because a run that hit its turn limit is not a game the brain lost.
    """
    if not state:
        return "unresolved", "the game never answered game-state"
    if not state.get("done"):
        return "unresolved", f"no STATE_GAME_DONE at turn {state.get('turn')}"

    kind = state.get("victory", "none")
    bases = state.get("bases") or {}
    ours = bases.get(our_faction, 0)
    rivals = {f: n for f, n in bases.items() if f != our_faction and n > 0}

    if kind in VICTORY_KINDS:
        # The flags say a victory happened, not whose. Holding bases when no rival does is the
        # unambiguous case; anything else is reported as won-by-someone and left for a human.
        if ours > 0 and not rivals:
            return "win", f"{kind} victory, sole surviving faction"
        return "loss", f"{kind} victory with rivals still holding bases {sorted(rivals)}"
    if ours == 0:
        return "loss", "eliminated — no bases held"
    return "unresolved", f"game done with victory={kind} and {ours} bases held"


def load(path: Path) -> dict[tuple[int, str], dict]:
    rows: dict[tuple[int, str], dict] = {}
    if not path.is_file():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        rows[(row["seed"], row.get("arm", "brain"))] = row
    return rows


def save(path: Path, rows: dict[tuple[int, str], dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for key in sorted(rows):
            fh.write(json.dumps(rows[key], sort_keys=True) + "\n")


def cmd_report(args: argparse.Namespace) -> int:
    rows = load(Path(args.results))
    if not rows:
        print(f"no results in {args.results}", file=sys.stderr)
        return 1

    # THE REFUSAL. Seeds played under different fairness profiles are not a ladder.
    profiles = {json.dumps(profile_key(r.get("fairness") or {})) for r in rows.values()}
    if len(profiles) > 1:
        print(
            "refusing: these seeds did not share a fairness profile, so the ladder is "
            "measuring the handicap and not the brain.",
            file=sys.stderr,
        )
        for row in sorted(rows.values(), key=lambda r: (r["seed"], r.get("arm", ""))):
            fair = row.get("fairness") or {}
            print(
                f"  seed {row['seed']} arm {row.get('arm')}: "
                f"slot={fair.get('slot')} difficulty={fair.get('difficulty')}",
                file=sys.stderr,
            )
        return 1

    by_arm: dict[str, list[dict]] = {}
    for row in rows.values():
        by_arm.setdefault(row.get("arm", "brain"), []).append(row)

    for arm, seeds in sorted(by_arm.items()):
        wins = [r for r in seeds if r["outcome"] == "win"]
        losses = [r for r in seeds if r["outcome"] == "loss"]
        unresolved = [r for r in seeds if r["outcome"] == "unresolved"]
        print(f"\narm {arm}: {len(wins)} win / {len(losses)} loss / {len(unresolved)} unresolved")
        for row in sorted(seeds, key=lambda r: r["seed"]):
            print(
                f"  seed {row['seed']:<6} {row['outcome']:<11} turn {row.get('turn', '?'):<5} "
                f"{row.get('why', '')}"
            )
        # na-xb1's acceptance, stated rather than computed into a boolean nobody can check.
        if len(seeds) >= 3:
            print(
                f"  goal (2 of 3 seeded wins): {len(wins)} of {len(seeds)}"
                f"{' — MET' if len(wins) >= 2 and len(seeds) >= 3 else ''}"
            )
        if unresolved:
            print(
                f"  NOTE: {len(unresolved)} seed(s) unresolved. An unfinished game is not a "
                "loss, and counting it as one flatters whichever side timed out."
            )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    print(
        "refusing: `run` needs one prepared save per seed, and this fork has no seam for\n"
        "starting a seeded game unattended (see na-clk). Preparing them by hand is a human\n"
        "action; the ladder will not pretend a shared save is a seeded ladder.\n\n"
        "What exists today: `report` scores results any runner produces, and `game-state` on\n"
        "the command channel is the reading it scores. See the bead for the seam that is\n"
        "missing and what it would take.",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="play the ladder (needs per-seed saves — see na-clk)")
    run.add_argument("--seeds", default="")
    run.add_argument("--out", default="evals/runs/na-clk/results.jsonl")
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="score a results file")
    rep.add_argument("results")
    rep.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
