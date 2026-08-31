#!/usr/bin/env python3
"""One decision's reasoning, reaching a later turn's decision — aegis-n8zmq's acceptance.

The bead asks for "a run that demonstrably carries a decision's rationale across >1 turn". This
is that run, reduced to what it actually needs: **no game**. The orchestrator is the thing under
test, a captured world view is a legitimate input to it, and the directive store is a file — so
the whole chain is reproducible on a fresh clone by anyone who doubts the result.

Three steps, and the middle one is the claim:

  1. a `faction.tech` decision at turn T is asked, with a directive store pointed at a path that
     DOES NOT EXIST — so any plan can only have been created by the issue itself. This is na-43h's
     own control and it is kept for the same reason.
  2. a `base.hurry` decision at turn T+n is asked through the SAME store.
  3. the second record is inspected: was the directive `in_force`, and was it `followed`.

`carry_report.py` then reads the log the two decisions wrote, which is what makes this a movement
of the project's own baseline rather than a bespoke demonstration.

**What would make this prove nothing**, both guarded:

  * a directive that was hand-written rather than issued. The store path is asserted absent
    first, and the run refuses if step 1 issues nothing rather than continuing to a step 2 that
    would report a satisfying `in_force` for somebody else's plan.
  * a second decision on the SAME turn. That is bulk work, not carry, and `carry_report.py`
    excludes tier `plan` for the same reason. The turns are asserted to differ.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "orchestrator" / "src"))
sys.path.insert(0, str(REPO / "scripts"))


def build(brain_kind: str, plan_path: Path):
    from decision_stability import build_brain  # noqa: PLC0415
    from neural_amplifier.directives import DirectiveStore  # noqa: PLC0415
    from neural_amplifier.orchestrator import Orchestrator  # noqa: PLC0415
    from neural_amplifier.service import build_guard  # noqa: PLC0415

    return Orchestrator(
        brain=build_brain(brain_kind),
        retriever=None,
        guard=build_guard(None),  # type: ignore[arg-type]
        plan=DirectiveStore(plan_path),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--issuing", type=Path, required=True, help="observations with faction.tech")
    ap.add_argument("--later", type=Path, required=True, help="observations with the later surface")
    ap.add_argument("--later-surface", default="base.hurry")
    ap.add_argument("--plan", type=Path, required=True, help="store path; MUST NOT EXIST")
    ap.add_argument("--brain", default="claude-code")
    ap.add_argument("--log", type=Path, help="write both decision records here, for carry_report")
    ap.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="ask the issuing surface up to this many times; a model that issues on some runs "
        "and not others still demonstrates carry, and pretending otherwise would need a "
        "guaranteed-issuing brain, which is the scripted driver na-j2w says proves nothing",
    )
    args = ap.parse_args()

    if args.plan.exists():
        raise SystemExit(
            f"refusing: {args.plan} already exists. The control this run depends on is that any\n"
            "directive found later could only have been created by the issue itself."
        )

    from decision_stability import load_observation, to_world_view  # noqa: PLC0415

    issuing_view = to_world_view(load_observation(args.issuing, "faction.tech"))
    later_view = to_world_view(load_observation(args.later, args.later_surface))
    if issuing_view.turn == later_view.turn:
        raise SystemExit(
            f"refusing: both world views are turn {issuing_view.turn}. Two decisions on one turn\n"
            "is bulk work, not carry — the whole claim is that the turns differ."
        )

    orchestrator = build(args.brain, args.plan)

    issued: list[dict] = []
    attempts = 0
    while attempts < args.attempts and not issued:
        attempts += 1
        first = orchestrator.decide(issuing_view)
        issued = [
            d if isinstance(d, dict) else d.model_dump(mode="json")
            for d in (first.orders.directives or [])
        ]

    if not issued:
        print(
            json.dumps(
                {
                    "carried": False,
                    "why": "the issuing surface issued no directive",
                    "attempts": attempts,
                    "issuing_turn": issuing_view.turn,
                },
                indent=2,
            )
        )
        return 2

    second = orchestrator.decide(later_view)
    plan_block = second.record.plan
    in_force = list(getattr(plan_block, "in_force", []) or [])
    followed = list(getattr(plan_block, "followed", []) or [])
    ids = {d.get("id") for d in issued}

    result = {
        "carried": bool(ids & set(in_force)),
        "followed": bool(ids & set(followed)),
        "issuing_turn": issuing_view.turn,
        "later_turn": later_view.turn,
        "turns_carried": later_view.turn - issuing_view.turn,
        "attempts": attempts,
        "issued": issued,
        "later_in_force": in_force,
        "later_followed": followed,
        "later_unmeasurable": list(getattr(plan_block, "unmeasurable", []) or []),
        "plan_file_bytes": args.plan.stat().st_size if args.plan.exists() else 0,
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.log:
        with args.log.open("w", encoding="utf-8") as handle:
            for record in (first.record, second.record):
                payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else {}
                handle.write(json.dumps(payload) + "\n")

    return 0 if result["carried"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
