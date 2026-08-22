"""Why a unit was sent somewhere, and what would make that wrong (na-7bk slice 3).

The engine already persists the ORDER: `set_move_to` keeps a multi-turn goto and door 2's `move`
issues one, so a unit walks east for six turns without anyone re-deciding. What the engine does
not keep is the REASON — so a later decision sees a unit walking east and cannot tell a
considered ferry run from a stale order nobody cancelled. Both look identical, and the safe
reading of an unexplained order is to leave it alone, which is how a stale one survives.

An intent is that missing half. The engine executes, the graph remembers why, and the agent
re-decides only when something it named in advance changes.

**Triggers are held to the same standard as a queued answer's predicates, and it costs more
here.** `queued.py` refuses a predicate outside the measured metric vocabulary because an
uncheckable one can never fire, which turns a standing answer into a script. The same argument
applies to an intent — an intent whose review triggers cannot fire is not a plan under review, it
is a plan nobody will ever revisit, which is strictly worse than having written nothing because
it reads in the prompt as though it were being watched.

The cost is higher because the triggers the design names are mostly not expressible today:

  enemy unit within N tiles of the path   — needs a tile model. The orchestrator has none, BY
                                            DESIGN: door 2's move refuses an unexplored
                                            destination via the engine's own `is_known`, and the
                                            engine gate is deliberately the only tile gate.
  destination tile ownership changed      — same.
  unit damaged below X                    — needs per-unit state in the world view; the base
                                            surfaces carry base and faction metrics, not units.
  war state change                        — `at_war` is not in the metric vocabulary.

So what can be expressed is the horizon, plus faction-scope metric triggers (`energy_reserves`
collapsing, `drone_total` rising, `military_units` falling) which are real and do fire. That is
narrower than the design imagined and it is stated rather than papered over: widening the
vocabulary to names nothing populates would produce exactly the never-firing trigger this module
exists to refuse. Making the rest expressible is an ADAPTER change — thinker has to emit the
facts first — not an orchestrator one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .queued import Predicate, QueueError
from .queued import validate as validate_predicates


class IntentError(ValueError):
    """An intent that could not be accepted. Raised where one is RECORDED, never where recalled."""


@dataclass
class UnitIntent:
    """A long-horizon order, the reason for it, and what would put it back in front of the agent.

    `triggers` reads the same way a queued answer's predicates do: the conditions under which the
    intent REMAINS worth pursuing. Stated positively so the same comparison checks both, and so a
    metric this world view omits fails the check rather than passing it silently.
    """

    unit_id: int
    goal: str
    rationale: str | None = None
    #: The turn by which this should have happened. Required in practice — an intent with no
    #: horizon is the standing order nobody revisits, which is the failure this bead exists to
    #: avoid — but modelled as optional so a caller who omits it is REFUSED with a reason rather
    #: than silently given a default that means "forever".
    until_turn: int | None = None
    triggers: list[Predicate] = field(default_factory=list)

    def line(self) -> str:
        """The prompt line. What the agent reads when this unit comes up again."""
        parts = [f"unit {self.unit_id}: {self.goal}"]
        if self.rationale:
            parts.append(f"because {self.rationale}")
        if self.until_turn:
            parts.append(f"by turn {self.until_turn}")
        if self.triggers:
            parts.append("revisit if not " + " and ".join(t.describe() for t in self.triggers))
        return "; ".join(parts)

    def summary(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "goal": self.goal,
            "rationale": self.rationale,
            "until_turn": self.until_turn,
            "triggers": [t.describe() for t in self.triggers],
        }

    def review_due(self, world_view: Any) -> tuple[bool, list[str]]:
        """Should the agent look at this again, and why?

        Returns `(due, reasons)`. An intent with no live trigger is not "fine" — it is unreviewed,
        which is why `validate` refuses one.
        """
        turn = getattr(world_view, "turn", None)
        if self.until_turn is not None and turn is not None and turn > self.until_turn:
            return True, [f"horizon passed: turn {turn} is past {self.until_turn}"]
        measurements = getattr(world_view, "metrics", None) or {}
        broken = [why for ok, why in (t.check(measurements) for t in self.triggers) if not ok]
        return bool(broken), broken


def validate(intent: UnitIntent) -> None:
    """Refuse an intent nothing could ever bring back for review.

    Two ways that happens, and both are refused here rather than discovered later:

    - no horizon: it is never finished, so it never leaves the prompt;
    - no triggers: nothing about the board can make it wrong, so it is a note rather than a plan.

    Either alone is enough to make an intent unreviewable, and an unreviewable intent is worse
    than none: it sits in every later world view reading as a plan under review.
    """
    if intent.until_turn is None:
        raise IntentError(
            "an intent needs an until_turn; without a horizon it never becomes finished and "
            "never leaves the prompt, which is the standing order nobody revisits"
        )
    if intent.unit_id is None:
        raise IntentError("an intent needs the unit it is about")
    if not intent.goal:
        raise IntentError("an intent needs a goal; the engine already knows the order")
    try:
        validate_predicates(intent.triggers)
    except QueueError as exc:
        # Re-raised as an IntentError with the queue's message intact — the vocabulary rule is
        # shared on purpose (one grammar for the agent to learn), but a caller recording an
        # intent should not have to know that a queue module enforced it.
        raise IntentError(str(exc)) from exc
