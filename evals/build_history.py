"""na-61c.2 — does recent build history stop a base flip-flopping?

**Answered: yes, and without becoming an anchor.**

A stable decision proves nothing here: the brain cannot flip-flop on a choice it already makes
unanimously, so history could only fail to help. The eval therefore runs on a *contested*
decision — the same `base.production` world view grounded with four facts splits three ways —
and puts the option the brain picks **least** often into the history. Reinforcing what it
already preferred would be unfalsifiable; asking it to continue the option it usually rejects
is the version that can fail.

Arms:

``nohistory``  the contested decision as-is.
``history``    three prior turns, all `llm`, all naming the minority option.
``changed``    the same history, but the case has changed under it.

The third arm is what makes the other two mean anything. History alone cannot separate
"correctly continued" from "anchored regardless", because nothing in the second arm argues for
switching — continuing is right by construction. So the third moves something real: drones
triple and a priority-8 directive caps them, which the history's item does not address and
another option does. Continuing *there* is the failure worth catching, and it is worse than
flip-flopping: it launders one early choice into permanent policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness import base_production, ground, load_answers, spread, tally

from neural_amplifier.contract import Directive, DirectiveStatus, PriorChoice

#: The four facts this eval was measured with, named rather than derived.
#:
#: They were originally whatever `ground(view, retriever, 4)` returned, which at the time meant
#: the top four by information value — the retriever ranked, briefly, before na-373 refuted it.
#: Reverting that silently changed this eval: the same call now yields the first four in
#: action-space order, a different set of facts and so a different experiment under the same
#: name, with the committed answers still sitting beside it.
#:
#: So the condition is pinned here. An eval's arms are part of the eval; deriving them from
#: whatever a retriever currently does means the experiment changes whenever the retriever does,
#: which is exactly what happened.
CONTESTED = (
    "fac:command-center",
    "fac:research-hospital",
    "fac:children-s-creche",
    "fac:recreation-commons",
)

#: The option the contested arm picks least often. Continuing it is the behaviour under test.
MINORITY = "build:7"


def arms(links: Path, keep: int | None = None) -> dict[str, Any]:
    view, retriever = base_production(links)
    grounded = ground(view, retriever)
    by_id = {ln.split(" ", 1)[0]: ln for ln in (grounded.grounding or [])}
    missing = [f for f in CONTESTED if f not in by_id]
    if missing:
        raise SystemExit(f"eval condition no longer retrievable: {', '.join(missing)}")
    contested = grounded.model_copy(update={"grounding": [by_id[f] for f in CONTESTED]})
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


def score(out: Path, links: Path, keep: int | None = None) -> None:
    print(f"{'arm':12} {'n':>3}  {'continued':>9}  choices")
    for name in ("nohistory", "history", "changed"):
        rows = load_answers(out, name)
        if not rows:
            continue
        counts = tally(rows)
        kept = counts.get(MINORITY, 0)
        print(f"{name:12} {len(rows):>3}  {kept / len(rows):>9.2f}  {spread(counts)}")
    print(f"\n'continued' = chose {MINORITY}, the option the history names.")
    print("Expect: low, high, low. A high 'changed' means history overrides judgement.")
