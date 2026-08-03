"""The whole turn, not the oldest decision — what is coming, what arrived, what happened.

`/agent/next` hands over one decision at a time because that is genuinely all the orchestrator
knows: when base #1 is asked, bases #2..#51 have not been POSTed yet and do not exist to the
queue. An agent cannot spend a limited pool where it matters most across fifty bases if it only
ever sees the one in front of it.

So the adapter announces the set up front, from `mod_turn_upkeep`, and this keeps it.

**An announcement is a FORECAST, not a promise**, and the distinction is the whole design. It is
made at the between-turns seam, from the board as it stood when the previous turn ended — before
anything this turn has happened. A base can be captured, starve, or finish a project, and then
the decision it was expected to raise never comes. An agent that treated the forecast as a
worklist would wait forever for it.

That is why status is tracked per entry rather than as a count, and why `expected` and `raised`
are different words here. A turn where 51 were forecast and 47 arrived is an ordinary turn, not
an error — but it is also exactly what a stuck adapter looks like, so the numbers are reported
side by side and never collapsed into one.
"""

from __future__ import annotations

import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contract import WorldView
from .outcomes import EngineOutcome

Status = Literal["expected", "raised", "answered", "applied", "diverged"]


def _extra(world_view: WorldView, name: str) -> object | None:
    """Read a field the contract keeps in pydantic *extras*.

    `WorldView` is ``extra="allow"``, and pydantic raises AttributeError for an extra that is
    absent rather than returning None — so plain attribute access works on every adapter record
    and explodes on the hand-written fixtures that omit the field. That asymmetry is why this
    exists: the failure only shows up on the inputs least like production.
    """
    return getattr(world_view, name, None)

#: Turn history to retain. Small on purpose: this is a live view of the turn being played, and
#: the decision log is where a run's history belongs.
DEFAULT_TURNS = 4


class ExpectedDecision(BaseModel):
    """One decision the adapter thinks it will raise this turn."""

    model_config = ConfigDict(extra="allow")

    surface_id: str
    faction_id: int | None = None
    base_id: int | None = None
    base: str | None = None


class TurnAnnouncement(BaseModel):
    """What the adapter expects the coming turn to ask about."""

    model_config = ConfigDict(extra="allow")

    turn: int
    run_id: str | None = None
    faction_id: int | None = None
    expected: list[ExpectedDecision] = Field(default_factory=list)


class DecisionSlot(BaseModel):
    """One decision's life within a turn: forecast, raised, answered, and what the engine did."""

    surface_id: str
    faction_id: int | None = None
    base_id: int | None = None
    base: str | None = None
    status: Status = "expected"
    #: Set once the decision actually arrives. Absent on an entry that was forecast and never came.
    traceparent: str | None = None

    def key(self) -> tuple[str, int | None]:
        return (self.surface_id, self.base_id)


class TurnView(BaseModel):
    """The turn as it stands right now."""

    turn: int
    run_id: str | None = None
    faction_id: int | None = None
    announced: bool = False
    decisions: list[DecisionSlot] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)

    #: Forecast entries that never arrived. Reported explicitly rather than left to be inferred
    #: from a count mismatch — "51 expected, 47 raised" tells you something is missing but not
    #: which, and the which is what an agent waiting on one of them needs.
    unraised: list[str] = Field(default_factory=list)


class TurnStore:
    """Turn announcements and the live status of every decision within them."""

    def __init__(self, keep: int = DEFAULT_TURNS) -> None:
        self._keep = keep
        self._turns: dict[int, dict[tuple[str, int | None], DecisionSlot]] = {}
        self._meta: dict[int, TurnAnnouncement] = {}
        #: traceparent -> (turn, key), so an outcome arriving later can find its slot without
        #: rescanning every turn.
        self._by_trace: dict[str, tuple[int, tuple[str, int | None]]] = {}
        self._current: int | None = None
        self._lock = threading.Lock()

    # -- writes ---------------------------------------------------------------

    def announce(self, announcement: TurnAnnouncement) -> int:
        """Record what the coming turn is expected to ask about.

        Re-announcing the same turn REPLACES it. A second announcement means the adapter reached
        the seam again, so its earlier forecast is stale by definition; merging the two would
        leave entries from a board that no longer exists.
        """
        with self._lock:
            slots: dict[tuple[str, int | None], DecisionSlot] = {}
            for item in announcement.expected:
                slot = DecisionSlot(
                    surface_id=item.surface_id,
                    faction_id=item.faction_id,
                    base_id=item.base_id,
                    base=item.base,
                )
                slots[slot.key()] = slot
            self._turns[announcement.turn] = slots
            self._meta[announcement.turn] = announcement
            self._current = announcement.turn
            self._evict()
            return len(slots)

    def note_raised(self, world_view: WorldView) -> None:
        """A decision actually arrived.

        An unforecast decision is ADDED rather than dropped. The forecast is the adapter's best
        guess from the previous turn's board; a decision that shows up unannounced is real and an
        agent needs to see it. Silently discarding it would make the view a record of what we
        predicted rather than of what is happening.
        """
        turn = world_view.turn
        if turn is None:
            return
        trace = world_view.traceparent()
        key = (world_view.surface_id or "", _extra(world_view, "base_id"))
        with self._lock:
            slots = self._turns.setdefault(turn, {})
            if turn not in self._meta:
                # Decisions before any announcement: still a turn, just an unannounced one.
                self._meta[turn] = TurnAnnouncement(turn=turn)
            self._current = turn if self._current is None else max(self._current, turn)
            slot = slots.get(key)
            if slot is None:
                slot = DecisionSlot(
                    surface_id=key[0],
                    faction_id=_extra(world_view, "faction_id"),
                    base_id=key[1],
                    base=_extra(world_view, "base"),
                )
                slots[key] = slot
            # Never walk a slot BACKWARDS. A base is asked several times per turn, and a replay
            # arriving after the answer must not reset it to "raised" — that would make an
            # answered decision look outstanding and invite a second answer.
            if slot.status == "expected":
                slot.status = "raised"
            if trace:
                slot.traceparent = trace
                self._by_trace[trace] = (turn, key)
            self._evict()

    def note_answered(self, world_view: WorldView) -> None:
        """The orchestrator returned orders for this decision."""
        turn = world_view.turn
        if turn is None:
            return
        key = (world_view.surface_id or "", _extra(world_view, "base_id"))
        with self._lock:
            slot = self._turns.get(turn, {}).get(key)
            if slot is not None and slot.status in ("expected", "raised"):
                slot.status = "answered"

    def note_outcome(self, outcome: EngineOutcome) -> None:
        """What the engine did, folded onto the decision it belongs to."""
        with self._lock:
            found = self._by_trace.get(outcome.traceparent)
            if found is None:
                return
            turn, key = found
            slot = self._turns.get(turn, {}).get(key)
            if slot is None:
                return
            # Divergence is terminal: it is the engine disagreeing with us, and a later "applied"
            # for the same decision must not paper over it.
            if outcome.event == "divergence":
                slot.status = "diverged"
            elif slot.status != "diverged":
                slot.status = "applied"

    # -- reads ----------------------------------------------------------------

    def view(self, turn: int | None = None) -> TurnView:
        with self._lock:
            target = turn if turn is not None else self._current
            if target is None or target not in self._turns:
                return TurnView(turn=target or 0, announced=False)
            slots = self._turns[target]
            meta = self._meta.get(target)
            decisions = sorted(
                slots.values(), key=lambda s: (s.surface_id, s.base_id if s.base_id is not None else -1)
            )
            counts: dict[str, int] = {}
            for slot in decisions:
                counts[slot.status] = counts.get(slot.status, 0) + 1
            unraised = [
                f"{s.surface_id}:{s.base if s.base else s.base_id}"
                for s in decisions
                if s.status == "expected"
            ]
            return TurnView(
                turn=target,
                run_id=meta.run_id if meta else None,
                faction_id=meta.faction_id if meta else None,
                announced=bool(meta and meta.expected),
                decisions=[s.model_copy() for s in decisions],
                counts=counts,
                unraised=unraised,
            )

    def turns(self) -> list[int]:
        with self._lock:
            return sorted(self._turns)

    # -- housekeeping ---------------------------------------------------------

    def _evict(self) -> None:
        """Caller holds the lock."""
        while len(self._turns) > self._keep:
            oldest = min(self._turns)
            for slot in self._turns[oldest].values():
                if slot.traceparent:
                    self._by_trace.pop(slot.traceparent, None)
            del self._turns[oldest]
            self._meta.pop(oldest, None)
