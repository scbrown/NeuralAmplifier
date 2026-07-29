"""Token budget discipline for retrieval — ``docs/quipu-integration.md`` (K2).

The live risk in the knowledge layer is not correctness, it is cost: a prompt
that grows with the rulebook and a per-turn latency nobody notices until a
hundred-turn game. Bounding retrieval to the action space handles the common
case; this handles the tail, where a turn legitimately offers many choices.

Two rules, and the second is the one that matters:

- **Bound the total, not just the count.** A cap on facts says nothing about
  prompt size when one fact is a paragraph.
- **Drop tactics before rules.** Rules are correctness — a canonical cost or
  prerequisite the model must not get wrong. Tactics are optimization. Shedding
  the optimization first is also exactly the retrieval precedence the prompt
  enforces (``knowledge-architecture.md`` §Precedence), so the budget cannot
  quietly invert the trust ordering to save a few tokens.

**Nothing is dropped silently.** A truncated prompt that reads as complete is
the same failure shape as the all-fallback run: the decision looks informed and
is not. :class:`Budgeted` carries what was shed, and it lands on the record.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from ..knowledge import Grounding

#: Retrieval classes, in precedence order — first is shed last.
Kind = Literal["rule", "tactic"]
PRECEDENCE: tuple[Kind, ...] = ("rule", "tactic")

#: Rough characters-per-token. Deliberately pessimistic: a budget that
#: overestimates capacity is a budget that does not bind.
CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True)
class Fact:
    """One retrieved fact and how far it can be trusted."""

    text: str
    kind: Kind = "rule"

    @property
    def cost(self) -> int:
        """Approximate tokens. Exact counting needs the model's tokenizer,
        which is not worth a round-trip to decide whether to keep a sentence."""
        return max(1, round(len(self.text) / CHARS_PER_TOKEN))


@dataclass
class Budgeted:
    """What survived the budget, and what did not."""

    kept: list[Fact] = field(default_factory=list)
    dropped: list[Fact] = field(default_factory=list)
    spent: int = 0
    limit: int = 0

    @property
    def within_budget(self) -> bool:
        return not self.dropped

    def dropped_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for fact in self.dropped:
            counts[fact.kind] = counts.get(fact.kind, 0) + 1
        return counts

    def grounding(self) -> Grounding:
        """The surviving facts, plus an explicit note when anything was shed.

        The note is in the prompt on purpose: a model told "3 tactics omitted
        for budget" can say its answer is under-informed. One told nothing
        cannot.
        """
        facts = [f.text for f in self.kept]
        if self.dropped:
            summary = ", ".join(
                f"{count} {kind}{'s' if count != 1 else ''}"
                for kind, count in sorted(self.dropped_by_kind().items())
            )
            facts.append(f"[{summary} omitted for token budget]")
        return Grounding(facts=tuple(facts))


def apply_budget(facts: Iterable[Fact], limit: int) -> Budgeted:
    """Keep as much as fits, shedding the least-trusted class first.

    Within a class, order is preserved — the retriever already ranked by the
    action space, and reordering here would silently change which choice the
    model sees explained first.
    """
    ordered = list(facts)
    result = Budgeted(limit=limit)
    if limit <= 0:
        result.dropped = ordered
        return result

    # Walk the precedence order, filling from the most trusted class down. A
    # single greedy pass over the original order would let a long tactic near
    # the front crowd out a rule behind it.
    keep: set[int] = set()
    spent = 0
    for kind in PRECEDENCE:
        for index, fact in enumerate(ordered):
            if fact.kind != kind:
                continue
            if spent + fact.cost > limit:
                # Keep scanning: a later, shorter fact of the same class may
                # still fit, and skipping the rest would drop facts the budget
                # could actually afford.
                continue
            keep.add(index)
            spent += fact.cost

    result.kept = [f for i, f in enumerate(ordered) if i in keep]
    result.dropped = [f for i, f in enumerate(ordered) if i not in keep]
    result.spent = spent
    return result
