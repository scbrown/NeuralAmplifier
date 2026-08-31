#!/usr/bin/env python3
"""Does a HORIZON make a model set direction, where re-wording did not? (aegis-n8zmq)

The wording arm settled one thing and left one standing. na-j2w offered two explanations for a
model that never issues a directive: the prompt does not ask properly, or *"the world view may
simply not show a horizon worth planning over"*. The first is now measured false — 0 issued in 20
runs across both orderings, on top of na-j2w's own 20. This tests the second.

The treatment is `WorldView.trajectory`: each metric's `now`, its value at `t-5` / `t-10` /
`t-20` where those turns were actually observed, and `slope_per_turn`. It is the representation
na-6db said was missing when it found the brain reliably wrong about hurrying — *"Thinker
declining a hurry is the correct call about compounding, and the brain has no representation of
that anywhere in its world view"*.

**Both arms use a REAL decision and a REAL series.** The decision is the `faction.tech` world view
recorded at turn 115 of the na-6db brain arm; the trajectory is derived from that same arm's
own per-turn `metrics` blocks, turns 101-115. Nothing is synthesised, so a difference cannot be
an artefact of a fixture built to show one. It also exercises the short-series rule on real data:
the run starts at turn 101, so `t-20` genuinely does not exist at turn 115 and is absent rather
than filled.

    bare        the world view as the adapter recorded it
    trajectory  identical, plus the derived block — one field, nothing else changed

The brain arm rather than brain-directive on purpose: that arm had a hand-written directive in
force on every decision, and measuring whether a model ISSUES one while it is already being shown
one confounds issuing with following.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "orchestrator" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from neural_amplifier import trajectory as traj  # noqa: E402
from neural_amplifier.claude_code_brain import ClaudeCodeBrain  # noqa: E402
from neural_amplifier.orchestrator import Orchestrator  # noqa: E402
from neural_amplifier.service import build_guard  # noqa: E402

OBSERVATIONS = REPO / "evals" / "runs" / "na-6db" / "brain.faction7.jsonl"
DECISION_TURN = 115


def _records() -> list[dict]:
    return [json.loads(line) for line in OBSERVATIONS.read_text().splitlines() if line.strip()]


def build(arm: str):
    """The world view under test, and the series behind it."""
    from decision_stability import to_world_view  # noqa: PLC0415

    records = _records()
    decisions = [
        r
        for r in records
        if r.get("surface_id") == "faction.tech" and r.get("turn") == DECISION_TURN
    ]
    if not decisions:
        raise SystemExit(f"no faction.tech record at turn {DECISION_TURN} in {OBSERVATIONS}")

    view = to_world_view(decisions[-1])
    series = traj.derive(
        ((r["turn"], r.get("metrics") or {}) for r in records if isinstance(r.get("turn"), int)),
        DECISION_TURN,
    )
    if arm == "trajectory":
        view = view.model_copy(update={"trajectory": series})
    return view, series


def run(arm: str, runs: int, trail: Path | None) -> dict:
    view, series = build(arm)
    if arm == "trajectory" and not view.trajectory:
        raise SystemExit(
            "refusing: the treatment arm carries an EMPTY trajectory — that is two "
            "identical prompts and a confident number, which is na-j2w's own "
            "post-mortem."
        )
    if arm == "bare" and view.trajectory:
        raise SystemExit("refusing: the control arm carries a trajectory")

    orchestrator = Orchestrator(
        brain=ClaudeCodeBrain(issue_directives=True),
        retriever=None,
        guard=build_guard(None),  # type: ignore[arg-type]
    )

    choices: collections.Counter[str] = collections.Counter()
    issued: list[dict] = []
    degraded = 0
    for index in range(runs):
        result = orchestrator.decide(view)
        picked = ",".join(c.action_id for c in result.orders.choices) or "<none>"
        choices[picked] += 1
        mine = [
            d if isinstance(d, dict) else d.model_dump(mode="json")
            for d in (result.orders.directives or [])
        ]
        issued.extend(mine)
        degraded += int(result.record.degraded)
        if trail is not None:
            with trail.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "arm": arm,
                            "run": index + 1,
                            "choice": picked,
                            "issued": mine,
                            "degraded": result.record.degraded,
                        }
                    )
                    + "\n"
                )
                handle.flush()

    return {
        "arm": arm,
        "runs": runs,
        "turn": DECISION_TURN,
        "surface": "faction.tech",
        "trajectory": series if arm == "trajectory" else None,
        "issued_count": len(issued),
        "issued": issued,
        "degraded": degraded,
        "choices": dict(choices),
        "stability": max(choices.values()) / runs if choices else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm", choices=["bare", "trajectory"], required=True)
    ap.add_argument("-n", "--runs", type=int, default=10)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--trail", type=Path)
    ap.add_argument("--show", action="store_true", help="print the series and exit, no spend")
    args = ap.parse_args()

    if args.show:
        _view, series = build(args.arm)
        print(json.dumps(series, indent=2, sort_keys=True))
        return 0

    result = run(args.arm, args.runs, args.trail)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
