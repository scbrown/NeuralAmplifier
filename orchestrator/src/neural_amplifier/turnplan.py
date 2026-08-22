"""The bulk-turn plan table: strategy set once per turn, answered in milliseconds (na-7bk).

Per-decision agent latency is ~30-60 seconds of wake, reason and submit, and door 1 blocks
synchronously — decision N+1 does not exist until N is answered — so an agent answering a
six-faction AI round decision-by-decision makes the round painfully slow. The live session that
filed this bead worked around it with an out-of-repo executor answering from a hot-reloaded JSON
file; this module is that mechanism moved where it belongs, with the record kept honest.

The shape: the agent reads the turn forecast (``GET /agent/turn``), decides the whole turn at
its own pace, and installs a table of answers keyed by faction, surface and base — valid for
exactly ONE stated turn. The orchestrator answers covered decisions from the table without
waking anyone and queues only uncovered ones to the agent, which is the entire point.

**A plan entry needs no predicates, and that is not a lowering of the bar.** A queued answer
(`queued.py`) stands across turns, so it must name what would make it wrong — the board has
turns in which to change under it. A plan entry's horizon IS the turn it names: it was written
against the same board it answers, and it dies with it. The one check that still applies is
invariant 1's backstop — the action must still be in the space the engine offers, because an
action space is rebuilt every time the engine asks and an item already built is simply gone.

**Tier ``plan`` is a fifth thing.** Not ``llm`` (no brain was asked for this decision), not
``deterministic`` (an agent chose it, deliberately, for this exact turn), not ``queued`` (nothing
conditional is standing — the table cannot outlive its turn), not ``deferred`` (nothing is
parked). Keeping it distinct is what lets replay tell strategy-driven answers from agent-driven
ones afterwards, which the bead names as a requirement.

**Fog applies.** A plan is faction-private state: ``faction_id`` is part of the key and comes
from the installer, never inferred — bases are numbered in one engine-wide sequence, so a base
id alone is a coincidence away from applying one faction's strategy to another's decision.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .contract import WorldView
from .turns import _extra


class PlanError(ValueError):
    """A plan that could not be accepted. Raised only where one is INSTALLED."""


@dataclass
class PlanEntry:
    """One decision the agent has already made for the planned turn."""

    surface_id: str
    action_id: str
    #: Which base this answers for. ``None`` means the surface's faction-scope decision
    #: (faction.se and the like, which have no base).
    base_id: int | None = None
    reason: str | None = None
    #: How often this entry answered. On a surface the engine re-asks within a turn (21/24
    #: base-turns fired twice, measured), one entry legitimately applies more than once.
    applied: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "base_id": self.base_id,
            "action_id": self.action_id,
            "reason": self.reason,
            "applied": self.applied,
        }


@dataclass
class TurnPlan:
    """One faction's table for one turn."""

    faction_id: int
    turn: int
    entries: dict[tuple[str, int | None], PlanEntry] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "faction_id": self.faction_id,
            "turn": self.turn,
            "entries": [e.summary() for e in self.entries.values()],
        }


class PlanStore:
    """The installed plans, one per faction, each pinned to its turn.

    Locked like the other decision-path stores: ``/decide`` runs on the game's blocked worker
    thread while the agent installs and reads on request threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plans: dict[int, TurnPlan] = {}
        #: Entries whose action had left the space by the time their decision arrived. Kept for
        #: the record: each one is a plan written against a board that changed before the engine
        #: asked, and a table that misses often is strategy set too early.
        self.missed: list[dict[str, Any]] = []

    def install(self, plan: TurnPlan) -> TurnPlan:
        """Install a faction's table for a turn, replacing whatever it had.

        Replacement is whole-table, deliberately. The install is "here is my strategy for turn
        N", and merging into the previous call's entries would leave stale answers from an
        earlier reading of the same turn standing behind the new ones — the exact confusion a
        per-turn table exists to rule out. An empty table is a legitimate install: it means
        "wake me for everything", which is the pre-plan behaviour.
        """
        with self._lock:
            self._plans[plan.faction_id] = plan
            return plan

    def find(self, world_view: WorldView) -> PlanEntry | None:
        """The entry covering this decision, or None.

        Faction FIRST, then the turn — a table for turn N answers nothing on turn N+1, however
        well its entries seem to match. By then the engine has played turn N, so those answers
        were made against a board that no longer exists; silence here is what re-raises the
        decision to the agent, which is the honest fallback.
        """
        faction_id = _extra(world_view, "faction_id", int)
        if faction_id is None or world_view.turn is None:
            return None
        with self._lock:
            plan = self._plans.get(faction_id)
            if plan is None or plan.turn != world_view.turn:
                return None
            return plan.entries.get(
                (world_view.surface_id or "", _extra(world_view, "base_id", int))
            )

    def miss(self, faction_id: int, entry: PlanEntry, why: str) -> None:
        """Retire an entry whose action the engine is no longer offering.

        Removed rather than left to fail again: the action space only rebuilds when the engine
        re-asks, and an entry naming a gone action can never apply for the rest of the turn.
        """
        with self._lock:
            plan = self._plans.get(faction_id)
            if plan is not None:
                plan.entries.pop((entry.surface_id, entry.base_id), None)
            self.missed.append({**entry.summary(), "faction_id": faction_id, "missed_because": why})

    def plans(self, faction_id: int | None = None) -> list[TurnPlan]:
        with self._lock:
            return [
                p for p in self._plans.values() if faction_id is None or p.faction_id == faction_id
            ]


def offered(entry: PlanEntry, world_view: WorldView) -> bool:
    """Is the planned action still one the engine is offering?

    The invariant-1 backstop, same as a queued answer's: the plan was written from the turn
    forecast, the space in front of us is the engine's current word, and only the engine's
    current word can be answered with.
    """
    return any(a.id == entry.action_id for a in (world_view.action_space or []))


def validate(plan: TurnPlan) -> None:
    """Refuse a table that could not answer anything, while the installer can still be told.

    The checks are shape, not legality — whether an action id is real is the engine's question,
    asked per decision by ``offered`` above. What is refused here is an entry that could never
    match (no surface) or never answer (no action).
    """
    for entry in plan.entries.values():
        if not entry.surface_id:
            raise PlanError("every plan entry needs a surface_id; without one it matches nothing")
        if not entry.action_id:
            raise PlanError(
                "every plan entry needs an action_id; a plan entry with no answer is a note, "
                "and the queue of uncovered decisions already handles those"
            )
