"""Does recent build history stop a base flip-flopping? (na-61c.2)

The bead asks for this to be *verified before building*, because the case weakened once decision
instability was measured properly. This is that check, minus a real game.

A stable decision proves nothing — the brain cannot flip-flop on a choice it makes unanimously,
so adding history to it could only fail to help. The test needs a **contested** decision, and
na-373 produced one: the same `base.production` world view grounded with four facts instead of
eight splits 15/3/2 across twenty runs.

So: take that contested world view, add a `history` block saying the last three turns chose the
*minority* option, and see whether the decision moves toward continuing it. Minority on purpose.
If history only reinforced what the brain already preferred it would be unfalsifiable; asking it
to continue the option it usually rejects is the version that can fail.

Two ways to fail, and they are opposite:

- **No movement** — history is decoration, and na-61c.2 should be dropped rather than built.
- **Total movement** — the brain follows history whatever the merits, which is worse than
  flip-flopping because it launders a bad early choice into a permanent one.

    python3 scripts/history_stability.py prompts --out runs/
    python3 scripts/history_stability.py score   --out runs/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from neural_amplifier.brain import _SYSTEM  # noqa: E402
from neural_amplifier.contract import (  # noqa: E402
    Directive,
    DirectiveStatus,
    PriorChoice,
)
from retrieval_utilisation import ground, world_view  # noqa: E402

#: The option the contested run picks only 3 times in 20. Continuing it is the behaviour under
#: test, and it is the behaviour the brain is least inclined to produce on its own.
MINORITY = "build:7"


def configs(links: Path, keep: int) -> dict[str, object]:
    view, retriever = world_view(links)
    contested = ground(view, retriever, keep)
    item = next(a.action for a in view.action_space if a.id == MINORITY)
    turn = contested.turn
    with_history = contested.model_copy(
        update={
            "history": [
                PriorChoice(turn=turn - 3, item=item, tier="llm"),
                PriorChoice(turn=turn - 2, item=item, tier="llm"),
                PriorChoice(turn=turn - 1, item=item, tier="llm"),
            ]
        }
    )
    # The third arm, and the one that makes the other two mean something. History alone
    # cannot tell "correctly continued" from "anchored regardless": nothing in that world view
    # argues for switching, so staying is right by construction. Here something genuinely
    # changed — drones have tripled and a standing directive caps them — and Recreation
    # Commons ("Fewer Drones") addresses it while Children's Creche does not.
    #
    # Continuing anyway is the failure this arm exists to catch: history that overrides
    # judgement is worse than no history, because it launders one early choice into a policy.
    cap = Directive(
        id="hold-drones",
        intent="Keep unrest down; drones above the cap cost us production every turn.",
        metric="drone_total",
        comparator="at_most",
        target=2.0,
        priority=8,
    )
    changed = with_history.model_copy(
        update={
            "metrics": {**(contested.metrics or {}), "drone_total": 6},
            "directives": [
                DirectiveStatus(
                    directive=cap,
                    current=6.0,
                    satisfied=False,
                    detail="drone_total 6, directive requires at most 2",
                    via="unrest is rising in this base",
                )
            ],
        }
    )
    return {"nohistory": contested, "history": with_history, "changed": changed}


def cmd_prompts(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    for name, wv in configs(args.links, args.keep).items():
        task = (
            _SYSTEM
            + "\n\n---\n\nWorld view:\n\n"
            + wv.model_dump_json(indent=2)  # type: ignore[attr-defined]
            + "\n\n---\n\nRespond with ONLY a single JSON object on one line, no prose, no code"
            ' fence:\n{"choice": "<action_id you pick>", "cited": ["<grounding ids that'
            ' influenced you>"]}\n'
        )
        (args.out / f"{name}.task.txt").write_text(task)
        print(f"{name}: {args.out}/{name}.task.txt")


def cmd_score(args: argparse.Namespace) -> None:
    print(f"{'config':12} {'n':>3}  {'continued':>9}  choices")
    for name in ("nohistory", "history", "changed"):
        rows = []
        for path in sorted((args.out / "out").glob(f"{name}.*.json")):
            m = re.search(r"\{.*\}", path.read_text(), re.S)
            if m:
                rows.append(json.loads(m.group(0)))
        if not rows:
            continue
        counts: dict[str, int] = {}
        for r in rows:
            c = r.get("choice", "?")
            counts[c] = counts.get(c, 0) + 1
        kept = counts.get(MINORITY, 0)
        spread = " ".join(f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        print(f"{name:12} {len(rows):>3}  {kept / len(rows):>9.2f}  {spread}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["prompts", "score"])
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / "na-61c2")
    ap.add_argument("--links", type=Path, default=Path("/home/user/thinker/docs/alphax.txt"))
    ap.add_argument("--keep", type=int, default=4)
    args = ap.parse_args()
    {"prompts": cmd_prompts, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
