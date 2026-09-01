#!/usr/bin/env python3
"""Prove one commitment opens, is reviewed, and steers a later turn — aegis-n8zmq.

The three records are deliberately T/T+10/T+11: a strategic review opens a measurable
commitment, another review explicitly keeps or revises that same id against a later board, and a
tactical decision is then answered with the reviewed commitment in force. The store must not
exist before the run, so a hand-written plan cannot satisfy the proof accidentally.
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


def observation_at(path: Path, surface: str, turn: int) -> dict:
    """The last captured decision for one exact surface and turn."""
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("surface_id") == surface and row.get("turn") == turn and row.get("action_space"):
            found = row
    if found is None:
        raise SystemExit(f"no {surface} observation at turn {turn} in {path}")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--opening", type=Path, required=True, help="observations for review turn T")
    ap.add_argument("--review", type=Path, required=True, help="observations for review turn T+10")
    ap.add_argument("--later", type=Path, required=True, help="observations for decision T+11")
    ap.add_argument("--opening-surface", default="faction.tech")
    ap.add_argument("--review-surface", default="base.hurry")
    ap.add_argument("--later-surface", default="base.hurry")
    ap.add_argument("--opening-turn", type=int, required=True)
    ap.add_argument("--review-turn", type=int, required=True)
    ap.add_argument("--later-turn", type=int, required=True)
    ap.add_argument("--plan", type=Path, required=True, help="store path; MUST NOT EXIST")
    ap.add_argument("--brain", default="claude-code")
    ap.add_argument("--log", type=Path, help="write the three records here")
    ap.add_argument("--attempts", type=int, default=1, help="opening-review attempts")
    args = ap.parse_args()

    if args.plan.exists():
        raise SystemExit(
            f"refusing: {args.plan} already exists; an old hand-written plan could fake carry"
        )

    from decision_stability import to_world_view  # noqa: PLC0415

    opening_source = to_world_view(
        observation_at(args.opening, args.opening_surface, args.opening_turn)
    )
    review_source = to_world_view(
        observation_at(args.review, args.review_surface, args.review_turn)
    )
    later_view = to_world_view(observation_at(args.later, args.later_surface, args.later_turn))
    opening_view = opening_source.model_copy(update={"scope": "turn", "action_space": []})
    review_view = review_source.model_copy(update={"scope": "turn", "action_space": []})
    if not (
        review_view.turn == opening_view.turn + 10
        and later_view.turn == review_view.turn + 1
    ):
        raise SystemExit(
            f"refusing: expected T/T+10/T+11, got {opening_view.turn}/{review_view.turn}/"
            f"{later_view.turn}; this must measure review across time, not bulk work"
        )

    orchestrator = build(args.brain, args.plan)
    issued: list[dict] = []
    attempts = 0
    first = None
    while attempts < args.attempts and not issued:
        attempts += 1
        first = orchestrator.review(opening_view)
        issued = [
            d if isinstance(d, dict) else d.model_dump(mode="json")
            for d in (first.orders.directives or [])
        ]
    if first is None or not issued:
        print(
            json.dumps(
                {
                    "carried": False,
                    "why": "the opening strategic review issued no directive",
                    "attempts": attempts,
                    "opening_turn": opening_view.turn,
                },
                indent=2,
            )
        )
        return 2

    second = orchestrator.review(review_view)
    opened_ids = {str(d.get("id")) for d in issued if d.get("id")}
    reviewed_ids = set(second.record.plan.issued)
    ids = opened_ids & reviewed_ids
    if not ids:
        print(
            json.dumps(
                {
                    "carried": False,
                    "why": "T+10 review did not explicitly keep or revise the opened commitment",
                    "opened": sorted(opened_ids),
                    "reviewed": sorted(reviewed_ids),
                },
                indent=2,
            )
        )
        return 3

    third = orchestrator.decide(later_view)
    plan_block = third.record.plan
    in_force = list(plan_block.in_force)
    followed = list(plan_block.followed)
    result = {
        "carried": bool(ids & set(in_force)),
        "followed": bool(ids & set(followed)),
        "opening_turn": opening_view.turn,
        "review_turn": review_view.turn,
        "later_turn": later_view.turn,
        "turns_carried": later_view.turn - opening_view.turn,
        "attempts": attempts,
        "issued": issued,
        "reviewed": sorted(reviewed_ids),
        "later_in_force": in_force,
        "later_followed": followed,
        "later_unmeasurable": list(plan_block.unmeasurable),
        "plan_file_bytes": args.plan.stat().st_size if args.plan.exists() else 0,
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.log:
        with args.log.open("w", encoding="utf-8") as handle:
            for record in (first.record, second.record, third.record):
                handle.write(json.dumps(record.model_dump(mode="json")) + "\n")
    return 0 if result["carried"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
