"""The strategist's review cadence (docs/seats-and-policy.md §6; aegis-8fye.8, slice 1).

A seat whose ``strategist`` is not ``"none"`` is reviewed on a cadence: every N turns (default
10, ``NA_REVIEW_EVERY``), and early on a discontinuity read from metrics the adapter already
reports — ``base_count`` falling (a base lost) or ``factions_at_war`` rising (a war declared).
A review is a doorbell ring with the second fixed sentence and a review id generated here; the
strategist answers through routes that install without blocking (``/agent/directive``,
``/agent/plan``, ``/agent/policy``).

**The game never waits for it.** :meth:`ReviewScheduler.observe` runs on the ``/decide`` path,
so it does arithmetic and starts a daemon thread for the ring, and nothing else. A ring that
hangs for its full timeout costs that thread, not the turn.

**A missed review is recorded, and changes nothing.** If no directive, plan or policy write for
the seat lands before the next review falls due, a ``review_missed`` event goes on that seat's
own decision log, and the standing policy stays exactly as it was. Invariant 9 — the engine is
never blocked on a model — holds for policy seats because a silent strategist is a recorded
fact rather than a stall.

**Every review lands in the seat's campaign ledger** (slice 2, ``ledger.py``) once its outcome
is known: ``acted``, with the directive, plan and policy-change ids written while it was open, or
``missed``. The ledger write runs on a daemon thread too, so a slow store never holds a turn.
:meth:`ReviewScheduler.context` is what the strategist reads for a review (``GET /agent/review``):
the open review, the seat's open commitments and its last five reviews.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from .config import ConfigError, Seat
from .contract import WorldView
from .decisions import DecisionLog
from .doorbell import Doorbell
from .ledger import CONTEXT_REVIEWS, CampaignLedger

log = logging.getLogger(__name__)

#: The cadence when ``NA_REVIEW_EVERY`` is unset: the design's "every ten turns".
DEFAULT_EVERY = 10

Reason = Literal["cadence", "base_lost", "war_declared"]
WriteKind = Literal["directive", "plan", "policy"]


def every_from_env() -> int:
    """``NA_REVIEW_EVERY``, refused at startup when it is not a positive integer."""
    raw = os.environ.get("NA_REVIEW_EVERY", "")
    if not raw:
        return DEFAULT_EVERY
    try:
        every = int(raw)
    except ValueError as exc:
        raise ConfigError(f"NA_REVIEW_EVERY must be a positive integer, not {raw!r}") from exc
    if every < 1:
        raise ConfigError(f"NA_REVIEW_EVERY must be a positive integer, not {raw!r}")
    return every


def _metric(world_view: WorldView, name: str) -> float | None:
    value = (world_view.metrics or {}).get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


@dataclass
class Review:
    """One review: when it rang, why, and whether the strategist wrote anything since."""

    id: str
    turn: int
    due_turn: int
    reason: Reason
    answered_by: WriteKind | None = None
    #: The ids each write kind produced while the review was open, for the ledger's na:Review.
    produced: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {"id": self.id, "turn": self.turn, "due_turn": self.due_turn, "reason": self.reason}


@dataclass
class _SeatState:
    #: The turn the cadence counts from: the first one seen, then each review's own turn.
    anchor: int
    open: Review | None = None
    base_count: float | None = None
    factions_at_war: float | None = None
    rung: list[Review] = field(default_factory=list)


class ReviewScheduler:
    """Per-seat review cadence, early triggers and miss detection."""

    def __init__(
        self,
        seats: Mapping[int, Seat],
        doorbell: Doorbell,
        log: DecisionLog | None = None,
        every: int = DEFAULT_EVERY,
        ledger_for: Callable[[int], CampaignLedger | None] | None = None,
    ) -> None:
        if every < 1:
            raise ValueError("review cadence must be at least one turn")
        #: Only seats with a strategist; a reflex-only seat has nobody to review.
        self.seats = {fid for fid, seat in seats.items() if seat.strategist != "none"}
        self.doorbell = doorbell
        self.log = log
        self.every = every
        #: The seat's campaign ledger, by faction id; None (or a None result) records nothing.
        self.ledger_for = ledger_for
        self._state: dict[int, _SeatState] = {}
        self._counter = 0
        self._lock = threading.Lock()
        #: Every review_missed event, in order — also what the seat's log received.
        self.missed: list[dict[str, object]] = []
        #: The ring threads started, so a test can join them; never joined on the turn path.
        self.threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ turn path

    def observe(self, world_view: WorldView) -> Review | None:
        """Note a decision's world view; ring a review if one is due. Never blocks.

        Returns the review rung, if any. The ring itself runs on a daemon thread.
        """
        fid = world_view.faction_id
        turn = world_view.turn
        if fid is None or fid not in self.seats or turn is None:
            return None
        with self._lock:
            state = self._state.get(fid)
            if state is None:
                state = self._state[fid] = _SeatState(anchor=turn)
            self._expire(fid, state, turn)
            reason = self._discontinuity(state, world_view)
            if reason is None and turn >= state.anchor + self.every:
                reason = "cadence"
            if reason is None or state.open is not None:
                # An open review already asks the strategist to look; ringing again on a second
                # trigger inside it is noise, and the open one's miss check still stands.
                return None
            self._counter += 1
            review = Review(
                id=f"review-s{fid}-{self._counter}",
                turn=turn,
                due_turn=turn + self.every,
                reason=reason,
            )
            state.open = review
            state.anchor = turn
            state.rung.append(review)
        thread = threading.Thread(
            target=self.doorbell.ring_review, args=(review.id,), daemon=True, name=review.id
        )
        self.threads.append(thread)
        thread.start()
        return review

    def _discontinuity(self, state: _SeatState, world_view: WorldView) -> Reason | None:
        reason: Reason | None = None
        bases = _metric(world_view, "base_count")
        wars = _metric(world_view, "factions_at_war")
        if bases is not None:
            if state.base_count is not None and bases < state.base_count:
                reason = "base_lost"
            state.base_count = bases
        if wars is not None:
            if state.factions_at_war is not None and wars > state.factions_at_war:
                reason = reason or "war_declared"
            state.factions_at_war = wars
        return reason

    def _expire(self, fid: int, state: _SeatState, turn: int) -> None:
        review = state.open
        if review is None or turn < review.due_turn:
            return
        state.open = None
        self._record(fid, review)
        if review.answered_by is not None:
            return
        event: dict[str, object] = {
            "event": "review_missed",
            "seat": fid,
            "review_id": review.id,
            "reason": review.reason,
            "rung_turn": review.turn,
            "due_turn": review.due_turn,
            "turn": turn,
            "policy": "standing policy holds",
        }
        self.missed.append(event)
        log.info("seat %s missed review %s; standing policy holds", fid, review.id)
        if self.log is not None:
            try:
                self.log.write_event(fid, event)
            except OSError as exc:  # evidence degrades; it never fails the turn
                log.warning("could not record review_missed for seat %s: %s", fid, exc)

    def _record(self, fid: int, review: Review) -> None:
        """Write the closed review to the seat's ledger, off the turn path."""
        ledger = self.ledger_for(fid) if self.ledger_for is not None else None
        if ledger is None:
            return
        outcome = "acted" if review.answered_by is not None else "missed"

        def write() -> None:
            try:
                ledger.record_review(
                    review.id,
                    review.turn,
                    review.reason,
                    outcome,
                    directives=review.produced.get("directive", ()),
                    plans=review.produced.get("plan", ()),
                    policy_changes=review.produced.get("policy", ()),
                )
            except Exception as exc:  # evidence degrades; it never fails the turn
                log.warning(
                    "could not record review %s in seat %s's ledger: %s", review.id, fid, exc
                )

        thread = threading.Thread(target=write, daemon=True, name=f"ledger-{review.id}")
        self.threads.append(thread)
        thread.start()

    # ------------------------------------------------------------------ write path

    def note_write(self, faction_id: int | None, kind: WriteKind, ref: str | None = None) -> None:
        """A directive, plan or policy write landed for this seat: the open review is answered.

        ``ref`` is the id the write produced (a directive id, a plan's turn, a policy change),
        carried onto the review's ledger record.
        """
        if faction_id is None:
            return
        with self._lock:
            state = self._state.get(faction_id)
            if state is not None and state.open is not None:
                if state.open.answered_by is None:
                    state.open.answered_by = kind
                if ref:
                    state.open.produced.setdefault(kind, []).append(ref)

    def context(self, faction_id: int) -> dict[str, object]:
        """A review's prompt context: the open review, open commitments, the last reviews."""
        with self._lock:
            state = self._state.get(faction_id)
            review = state.open.summary() if state is not None and state.open else None
        ledger = self.ledger_for(faction_id) if self.ledger_for is not None else None
        body: dict[str, object] = {"review": review}
        body.update(
            ledger.context(CONTEXT_REVIEWS)
            if ledger is not None
            else {"open_commitments": [], "recent_reviews": []}
        )
        return body

    def status(self) -> dict[str, object]:
        """A summary for ``/health``: cadence, and each seat's open review and misses."""
        with self._lock:
            return {
                "every": self.every,
                "seats": {
                    str(fid): {
                        "open": state.open.id if state.open else None,
                        "rung": len(state.rung),
                        "missed": sum(1 for m in self.missed if m["seat"] == fid),
                    }
                    for fid, state in sorted(self._state.items())
                },
            }
