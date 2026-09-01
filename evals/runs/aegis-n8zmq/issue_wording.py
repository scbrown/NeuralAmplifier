#!/usr/bin/env python3
"""Does the JSON block's WORDING decide whether a model ever issues a directive? (aegis-n8zmq)

na-j2w measured zero directives in twenty runs of `faction.tech` and closed as *run and reported,
not answered*: with no arm issuing, there was no treatment. na-1gl found the cause in the
model-facing schema — `"Empty unless setting direction"`, nine words whose only actionable clause
points at empty — and fixed it in `25b2a72` to `"Issue when this choice binds later turns; else
empty"`. Its own close line says the rerun it unblocks is na-j2w's, and that rerun was never done.

**It also fixed one lane of two.** `response.py` is the structured-output schema the Anthropic SDK
lane fills in. `claude -p` has no structured-output mode, so `claude_code_brain._JSON_INSTRUCTION`
states the shape in words instead — and that block still leads with the suppressor:

    `directives` is usually `[]`. Issue one only on a decision whose reasoning should outlive
    the turn — see the section above for when that applies and what makes a plan checkable.

Suppressor first, trigger hedged with "only". That is the wording na-1gl replaced everywhere it
looked, and the `claude-code` lane is the one that matches how the game is now played.

So this is the same fix applied to the other lane, run as an experiment rather than committed on
the strength of the argument — because the argument is exactly the kind that na-373 shows can be
convincing and wrong. Two arms, one observation, `claude -p`, one fresh process per run:

    current   the wording as it ships
    trigger   trigger first, suppressor second — na-1gl's ordering, nothing else changed

The comparison is only meaningful if the two prompts really differ, so the runner **asserts the
substitution happened** before spending anything. na-j2w's own post-mortem is the reason: its two
arms could have differed in nothing but length and it would have reported a confident number.
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

from neural_amplifier import claude_code_brain  # noqa: E402
from neural_amplifier.orchestrator import Orchestrator  # noqa: E402
from neural_amplifier.service import build_guard  # noqa: E402

#: The shipped wording, quoted so a drift in the source is caught rather than silently measured.
CURRENT = (
    "`directives` is usually `[]`. Issue one only on a decision whose reasoning should outlive "
    "the\nturn — see the section above for when that applies and what makes a plan checkable."
)

#: na-1gl's ordering: what would make you issue, then the default. Same two facts, same clauses,
#: reversed — deliberately NOT a stronger instruction, because a treatment that also argues
#: harder cannot tell "order matters" from "we pushed more".
TRIGGER_FIRST = (
    "Issue a `directive` when this choice binds later turns — when its reasoning should outlive\n"
    "the turn and can be checked against a metric the world view reports. Otherwise `directives`\n"
    "is `[]`. See the section above for what makes a plan checkable."
)


def arm_prompt(arm: str) -> str:
    block = claude_code_brain._JSON_INSTRUCTION
    if CURRENT not in block:
        raise SystemExit(
            "the shipped wording this experiment pins is no longer in _JSON_INSTRUCTION.\n"
            "Re-read the block and update CURRENT before running: an arm that silently failed to\n"
            "substitute would compare a prompt against itself and report a confident zero."
        )
    return block if arm == "current" else block.replace(CURRENT, TRIGGER_FIRST)


def run(arm: str, observation: Path, surface: str, runs: int, trail: Path | None = None) -> dict:
    """Ask one surface `runs` times and report what came back.

    **Each run is flushed to `trail` as it completes.** A `claude -p` call on this surface takes
    minutes, so ten of them is a long-running job, and the first version of this wrote only at the
    end — a wall-clock timeout then destroyed a completed arm and left an empty file, which is the
    same class of loss as the single-slot order result in turn-scoped-play.md. Nine finished runs
    that nobody can read are worth exactly as much as zero.
    """
    from decision_stability import load_observation, to_world_view  # noqa: PLC0415

    prompt = arm_prompt(arm)
    if arm != "current" and prompt == claude_code_brain._JSON_INSTRUCTION:
        raise SystemExit("substitution produced an identical prompt — refusing to spend")

    original = claude_code_brain._JSON_INSTRUCTION
    claude_code_brain._JSON_INSTRUCTION = prompt
    try:
        world_view = to_world_view(load_observation(observation, surface))
        orchestrator = Orchestrator(
            brain=claude_code_brain.ClaudeCodeBrain(issue_directives=True),
            retriever=None,
            guard=build_guard(None),  # type: ignore[arg-type]
        )
        choices: collections.Counter[str] = collections.Counter()
        issued: list[dict] = []
        degraded = 0
        for index in range(runs):
            result = orchestrator.decide(world_view)
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
                                "degrade_reason": result.record.degrade_reason,
                            }
                        )
                        + "\n"
                    )
                    handle.flush()
    finally:
        claude_code_brain._JSON_INSTRUCTION = original

    return {
        "arm": arm,
        "runs": runs,
        "surface": surface,
        "prompt_chars": len(prompt),
        "issued_count": len(issued),
        "issued": issued,
        "degraded": degraded,
        "choices": dict(choices),
        "stability": max(choices.values()) / runs if choices else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("observations", type=Path)
    ap.add_argument("--surface", default="faction.tech")
    ap.add_argument("-n", "--runs", type=int, default=10)
    ap.add_argument("--arm", choices=["current", "trigger"], required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument(
        "--trail",
        type=Path,
        help="append one line per completed run, so a timeout cannot destroy finished work",
    )
    args = ap.parse_args()

    result = run(args.arm, args.observations, args.surface, args.runs, args.trail)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
