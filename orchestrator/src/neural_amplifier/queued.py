"""Standing answers that hold until the board says otherwise (na-7bk slice 2).

A deferral (`deferrals.py`) says *ask me later about this one*. A queued answer says *stop asking
me about this until something changes* — and names, in advance and in measurable terms, what
"something changes" means.

That last clause is the whole design. An answer that simply repeats is a script, and a script
keeps building Recycling Tanks through a drone riot. What makes this different from a script is
that the agent has to say what would make it wrong, in a vocabulary that can be checked against
the board every turn. Stiwi's framing: *queued decisions that can be changed later ONLY if a
tactical response is needed* — so the queue must be able to notice the tactical response is
needed without waking anybody up.

**Predicates are restricted to the measured metric vocabulary, and that is a refusal, not a
limitation.** `directives.py` already refuses a directive naming a metric nobody emits, because
an unmeasurable directive fails silently for the rest of the game rather than loudly at the
moment it was written. A queued answer with an uncheckable predicate is worse: the directive at
least only failed to *steer*, while this one keeps *answering* — its invalidation condition can
never fire, so it degrades into exactly the script it was supposed not to be. Refused at queue
time, while the decision that wrote it is still in the loop and can be told why.

**Tier `queued` is its own thing, for the same reason `deferred` is.** It is not `llm` (no brain
was asked for this decision) and not `deterministic` (an agent chose this answer, deliberately,
for a stated set of conditions). Collapsing it either way loses the ability to ask the only
question that matters about the mechanism afterwards: how often did a standing answer stand, and
how often was it overtaken?
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from .contract import WorldView
from .metrics import VOCABULARY, describe, known

#: Same comparators as a directive's, and deliberately the same words. A queued answer's
#: predicate and a directive's target are the same kind of claim about the board, so an agent
#: that has learned one grammar has learned both.
Comparator = Literal["at_least", "at_most"]

COMPARATORS: tuple[str, ...] = ("at_least", "at_most")


class QueueError(ValueError):
    """A queued answer that could not be accepted. Raised only where one is INSTALLED."""


@dataclass(frozen=True)
class Predicate:
    """A condition the queued answer depends on, in terms the board can settle.

    Reads as the condition under which the answer REMAINS valid — "hold while mineral_surplus is
    at_least 2" — rather than as the thing that invalidates it. Stating it positively is what
    makes it checkable by the same code that measures a directive, and it means a predicate that
    cannot be evaluated (a metric this world view omits) is a HOLD failure rather than a silent
    pass. Absence of evidence is not evidence the answer is still right.
    """

    metric: str
    comparator: Comparator
    target: float

    def describe(self) -> str:
        return f"{self.metric} {self.comparator.replace('_', ' ')} {self.target:g}"

    def check(self, measurements: dict[str, float]) -> tuple[bool, str]:
        """Does this predicate still hold, and what does the board say about it?"""
        current = measurements.get(self.metric)
        if current is None:
            # Fail-closed. A queued answer applying on the strength of a metric the adapter did
            # not send this turn is an answer standing on an unread instrument.
            return False, f"{self.metric} is not reported in this world view"
        if self.comparator == "at_least":
            ok = float(current) >= self.target
        else:
            ok = float(current) <= self.target
        return ok, f"{self.metric} is {float(current):g}, needed {self.describe()}"


def validate(predicates: Iterable[Predicate]) -> None:
    """Refuse a predicate that cannot be checked, while the agent can still be told why.

    Same rule and same reasoning as `directives.validate`, one step stricter in consequence: an
    unmeasurable directive fails to steer, an unmeasurable predicate keeps answering.
    """
    checked = list(predicates)
    if not checked:
        raise QueueError(
            "a queued answer needs at least one predicate; an answer with nothing that could "
            "invalidate it is a script, and the point of queueing is that it stops when the "
            "board changes"
        )
    for predicate in checked:
        if not known(predicate.metric):
            raise QueueError(
                f"unknown metric {predicate.metric!r}; a predicate may only reference "
                f"{', '.join(sorted(VOCABULARY))}"
            )
        if predicate.comparator not in COMPARATORS:
            raise QueueError(
                f"unknown comparator {predicate.comparator!r}; use one of {', '.join(COMPARATORS)}"
            )


@dataclass
class QueuedAnswer:
    """One standing answer, and the conditions it stands on."""

    #: Whose answer this is. Queued answers are faction-private state — the same boundary the
    #: memory scope draws (na-7bk fog ruling) — so this is part of the key, never a label.
    faction_id: int
    surface_id: str
    action_id: str
    predicates: list[Predicate]
    #: Which base this answers for. `None` means the surface's faction-scope answer (faction.se
    #: and the like, which have no base).
    base_id: int | None = None
    #: Last turn this may answer for. A queued answer with no horizon is a standing order nobody
    #: revisits, which is the failure mode this whole bead exists to avoid.
    until_turn: int | None = None
    reason: str | None = None
    #: Counted so the mechanism can be judged rather than assumed: how often a standing answer
    #: actually stood.
    applied: int = 0

    def key(self) -> tuple[int, str, int | None]:
        return (self.faction_id, self.surface_id, self.base_id)

    def covers(self, world_view: WorldView) -> bool:
        """Is this the decision this answer was queued for?

        Faction FIRST, and never inferred from the base id. Two factions' bases are numbered in
        one engine-wide sequence, so a base id alone would be a coincidence away from applying
        one faction's standing answer to another's decision.
        """
        from .turns import _extra

        if _extra(world_view, "faction_id", int) != self.faction_id:
            return False
        if (world_view.surface_id or "") != self.surface_id:
            return False
        return _extra(world_view, "base_id", int) == self.base_id

    def check(self, world_view: WorldView) -> tuple[bool, list[str]]:
        """Does this answer still stand, and if not, which predicates say otherwise?

        Returns EVERY violated predicate rather than the first. The agent is about to be woken
        for a decision it thought it had settled, and being told one of three reasons invites it
        to answer that one and re-queue into the next wake-up.
        """
        if (
            self.until_turn is not None
            and world_view.turn is not None
            and world_view.turn > self.until_turn
        ):
            return False, [f"horizon passed: turn {world_view.turn} is past {self.until_turn}"]
        measurements = world_view.metrics or {}
        violated = [why for ok, why in (p.check(measurements) for p in self.predicates) if not ok]
        return not violated, violated

    def offered(self, world_view: WorldView) -> bool:
        """Is the queued action still one the engine is offering?

        The invariant-1 backstop, applied to a decision the brain is not being asked about. A
        queued answer names an action id from the turn it was queued on, and an action space is
        rebuilt every time the engine asks — an item that has been built, or whose prerequisite
        was lost, is simply gone. Answering with it would be an id the engine never offered,
        which is the one failure a standing answer must not be able to cause silently.
        """
        return any(a.id == self.action_id for a in (world_view.action_space or []))

    def summary(self) -> dict[str, Any]:
        return {
            "faction_id": self.faction_id,
            "surface_id": self.surface_id,
            "base_id": self.base_id,
            "action_id": self.action_id,
            "predicates": [p.describe() for p in self.predicates],
            "until_turn": self.until_turn,
            "reason": self.reason,
            "applied": self.applied,
        }


class QueueStore:
    """The standing answers, keyed by faction, surface and base.

    Locked like the other decision-path stores: `/decide` runs on the game's blocked worker
    thread while the agent installs and reads on request threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple[int, str, int | None], QueuedAnswer] = {}
        #: Answers that stopped standing, kept for the record. An overtaken queue entry is the
        #: most interesting thing this mechanism produces — it is the moment the board did
        #: something the agent had said in advance would matter.
        self.retired: list[dict[str, Any]] = []

    def install(self, answer: QueuedAnswer) -> QueuedAnswer:
        validate(answer.predicates)
        with self._lock:
            self._items[answer.key()] = answer
            return answer

    def find(self, world_view: WorldView) -> QueuedAnswer | None:
        with self._lock:
            for item in self._items.values():
                if item.covers(world_view):
                    return item
            return None

    def retire(self, answer: QueuedAnswer, why: list[str]) -> None:
        """Take a standing answer out of service, and say what overtook it."""
        with self._lock:
            self._items.pop(answer.key(), None)
            self.retired.append({**answer.summary(), "retired_because": why})

    def standing(self, faction_id: int | None = None) -> list[QueuedAnswer]:
        with self._lock:
            return [
                i for i in self._items.values() if faction_id is None or i.faction_id == faction_id
            ]


def vocabulary_prompt() -> str:
    """The predicate vocabulary, for putting in front of an agent that must use it correctly."""
    lines = [
        "A queued answer holds until one of its predicates stops holding. Each predicate names",
        "a metric from this list, a comparator (at_least, at_most) and a number:",
    ]
    lines.extend(f"  - {describe(name)}" for name in sorted(VOCABULARY))
    return "\n".join(lines)
