"""na-vbe — can grounding drop what the action space already says, without moving the decision?

na-373 established the criterion this eval is measured against: retrieval may get cheaper only
if the *choice distribution* holds. It failed there because truncation drops whole facts, which
under-explains the particular options it drops. Compaction is the other way to get smaller —
keep every option's fact and shorten all of them the same way — so the question is whether
evenness is enough.

Two parts of a fact are candidates, because the action space carries better versions of both:

``cost``          the adapter ships a faction- and difficulty-adjusted *mineral* cost plus
                  turns-to-build; the graph holds the raw rulebook "rows". Emitting both put
                  ``cost 8`` beside ``cost 80`` about the same item. ``quipu.format_row`` had
                  excluded cost all along for exactly this reason and ``briefing.describe`` had
                  not, so the two retrievers disagreed.
``prerequisite``  the option is *in* the action space, so the engine has already ruled it
                  buildable and the tech gate is satisfied by construction.

Arms:

``verbose``  the full fact, as :data:`retrieval_ranking.VERBOSE` — cost and prerequisite kept.
``compact``  what ``DatalinksRetriever`` now ships: name, upkeep, effect.

**Both arms run on the same world view**, and that world view carries the adapter's cost fields.
That is not a detail. The first attempt at this eval scored compaction against a fixture whose
``action_space`` had no cost at all — see :func:`harness._adapter_action` — so the compact arm
had no price signal anywhere in its prompt and the measurement was of the fixture, not of the
change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness import base_production, ground, load_answers, spread, tally
from retrieval_ranking import VERBOSE


def _verbose(view: Any, retriever: Any) -> Any:
    """The compact arm's world view with the full facts pasted back over its grounding.

    Pinned text rather than a re-derivation: ``describe(compact=False)`` still exists, but an arm
    regenerated from live code moves whenever that code moves, which is exactly how na-61c2's
    arms drifted underneath its committed answers.
    """
    return ground(view, retriever).model_copy(update={"grounding": list(VERBOSE)})


def arms(links: Path) -> dict[str, Any]:
    view, retriever = base_production(links)
    return {"verbose": _verbose(view, retriever), "compact": ground(view, retriever)}


def score(out: Path, links: Path) -> None:
    """Utilisation, prompt size, and the choice distribution — the third one decides.

    Utilisation is reported because it is what compaction is *supposed* to move, and prompt size
    because it is what compaction *buys*. Neither is the criterion. A cheaper prompt that picks
    differently has not made retrieval more efficient, it has changed the brain, and na-373 is
    the standing reminder that those look identical in a utilisation table read on its own.
    """
    print(f"{'arm':9} {'n':>3} {'chars':>6} {'offered':>8} {'util':>6}  choices")
    for name in ("verbose", "compact"):
        rows = load_answers(out, name)
        if not rows:
            continue
        offered = set(json.loads((out / f"{name}.offered.json").read_text()))
        chars = len((out / f"{name}.task.txt").read_text())
        used = [len({c for c in dict.fromkeys(r.get("cited", [])) if c in offered}) for r in rows]
        util = sum(u / len(offered) for u in used) / len(rows) if offered else 0.0
        print(
            f"{name:9} {len(rows):>3} {chars:>6} {len(offered):>8} {util:>6.2f}"
            f"  {spread(tally(rows))}"
        )
