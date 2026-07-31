"""Decisions waiting for an agent to answer them.

The seam that lets an *external* brain — Claude Code, or any MCP client — sit where
:class:`~.brain.ClaudeBrain` sits, without the decision loop knowing the difference.

``Orchestrator.decide`` calls exactly one line to reach a model. Everything else it does —
fog gating, grounding, directives, action-space validation, the policy guard, the decision
record — happens around that line. So an agent-driven brain is not a new pipeline: it is a
:class:`~.brain.Brain` whose ``decide`` blocks here until somebody answers, and the world view
the agent collects is the *fully grounded* one, because it is assembled before the brain is
called.

Two parties meet on this queue and they are not in the same process:

* the **game side** — a ``POST /decide`` worker thread, holding a real socket open to the
  adapter, which blocks in :meth:`DecisionQueue.await_answer`;
* the **agent side** — an MCP client that :meth:`claim` s the decision and later
  :meth:`answer` s it.

The queue is deliberately not a message broker. It holds at most a handful of entries, because
each pending decision corresponds to one blocked HTTP worker — the game cannot run ahead of
itself. That bound is what makes an in-memory structure the right answer instead of the
beginning of a durability problem.
"""

from __future__ import annotations

import collections
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from .contract import Orders, WorldView

#: A pending decision's lifecycle. ``claimed`` exists so two attached agents cannot both answer
#: the same decision — the second one is told it was taken rather than silently losing the race.
Status = Literal["pending", "claimed", "answered", "abandoned"]

#: Hard cap on queue depth. Reaching it means decisions are being posted faster than they are
#: answered, which with one blocked worker per decision should be impossible — so hitting this
#: is a bug signal, not backpressure to be tuned.
MAX_DEPTH = 64

#: How many settled decisions keep an explanation. Enough to cover any plausible confusion
#: window for an agent that reconnects or compacts mid-turn.
SETTLED_MEMORY = 128


@dataclass
class Pending:
    """One decision waiting for an answer."""

    id: str
    world_view: WorldView
    created: float
    status: Status = "pending"
    orders: Orders | None = None
    #: Why this decision ended without an answer. Carried so the record says "the agent never
    #: answered" rather than the indistinguishable "the brain returned nothing".
    reason: str | None = None
    claimed_by: str | None = None
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def age_seconds(self) -> float:
        return time.monotonic() - self.created


class DecisionQueue:
    """Decisions posted by the game, claimed and answered by an agent.

    Thread-safe by construction: FastAPI runs a sync endpoint in a worker thread, so several
    ``POST /decide`` calls and several MCP tool calls can be in flight at once. One lock guards
    the map; each waiter has its own event so answering one decision does not wake the rest.
    """

    def __init__(self, max_depth: int = MAX_DEPTH) -> None:
        self._lock = threading.Lock()
        self._arrived = threading.Condition(self._lock)
        self._by_id: dict[str, Pending] = {}
        self._order: list[str] = []
        self._ids = itertools.count(1)
        self._max_depth = max_depth
        # Why each recently-settled decision closed, so a late answer gets told *what*
        # happened rather than merely that the id is unknown. An agent reads this text as a
        # tool result and acts on it: "abandoned, nobody answered in time" means re-read the
        # board, while "already answered" means it double-submitted. Collapsing both into
        # "no such decision" costs the model the difference.
        #
        # Bounded, because it is a courtesy and not a ledger — the decision log is where a
        # run's history actually lives.
        self._settled: collections.OrderedDict[str, str] = collections.OrderedDict()

    # ---------------------------------------------------------------- game side

    def post(self, world_view: WorldView) -> Pending:
        """Enqueue a decision and return its handle.

        Raises :class:`QueueFull` rather than blocking. A full queue means the agent side has
        stopped consuming, and the caller's correct response is to degrade to the deterministic
        tier immediately — waiting would convert one stuck decision into every decision stuck.
        """
        with self._lock:
            live = [i for i in self._order if self._by_id[i].status in ("pending", "claimed")]
            if len(live) >= self._max_depth:
                raise QueueFull(f"{len(live)} decisions already waiting")
            surface = world_view.surface_id or "decision"
            pending = Pending(
                id=f"{surface}-{next(self._ids)}",
                world_view=world_view,
                created=time.monotonic(),
            )
            self._by_id[pending.id] = pending
            self._order.append(pending.id)
            self._arrived.notify_all()
            return pending

    def await_answer(self, pending: Pending, timeout: float | None) -> Orders:
        """Block until this decision is answered, then return the orders.

        ``timeout=None`` waits forever, which is the configured default: a turn-based game
        pausing at a decision point is what it does for a human player too. The parameter exists
        because an unattended run needs an escape — a hung agent would otherwise hang the game
        with no way back to the deterministic tier — and one number is a cheap way to have both.

        Raises :class:`Unanswered` on timeout, having marked the decision abandoned so a late
        answer is rejected rather than applied to a turn that has moved on.
        """
        if not pending._done.wait(timeout):
            with self._lock:
                if pending.status in ("pending", "claimed"):
                    pending.status = "abandoned"
                    pending.reason = f"no answer within {timeout}s"
                    self._forget(pending.id)
            raise Unanswered(pending.reason or "abandoned")
        with self._lock:
            self._forget(pending.id)
            if pending.orders is None:
                raise Unanswered(pending.reason or "abandoned")
            return pending.orders

    def abandon(self, decision_id: str, reason: str) -> None:
        """Give up on a decision from the game side, releasing any waiter."""
        with self._lock:
            pending = self._by_id.get(decision_id)
            if pending is None or pending.status == "answered":
                return
            pending.status = "abandoned"
            pending.reason = reason
            self._forget(decision_id)
        pending._done.set()

    # --------------------------------------------------------------- agent side

    def claim(self, agent: str = "agent", wait: float = 0.0) -> Pending | None:
        """Take the oldest unclaimed decision, or ``None`` if there is none.

        ``wait`` lets a client without a doorbell block for one instead of spinning. With the
        tmux nudge wired up a decision is normally already waiting by the time this is called,
        so the default returns immediately.
        """
        deadline = time.monotonic() + wait
        with self._lock:
            while True:
                for decision_id in self._order:
                    pending = self._by_id[decision_id]
                    if pending.status == "pending":
                        pending.status = "claimed"
                        pending.claimed_by = agent
                        return pending
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._arrived.wait(remaining)

    def answer(self, decision_id: str, orders: Orders) -> Pending:
        """Submit orders for a claimed decision and release the waiting game thread.

        Rejects an unknown, already-answered or abandoned id with :class:`NotClaimable`, and the
        message goes back to the model. That is deliberate: an agent answering a decision the
        game has already moved past needs to be told so it can re-read the board, and silently
        accepting the orders would apply a stale choice to a live turn.
        """
        with self._lock:
            pending = self._by_id.get(decision_id)
            if pending is None:
                why = self._settled.get(decision_id)
                raise NotClaimable(
                    f"{decision_id} {why}" if why
                    else f"no decision {decision_id!r} — it may have already closed"
                )
            if pending.status == "answered":
                raise NotClaimable(f"{decision_id} was already answered")
            if pending.status == "abandoned":
                raise NotClaimable(f"{decision_id} was abandoned: {pending.reason}")
            pending.status = "answered"
            pending.orders = orders
        pending._done.set()
        return pending

    # ------------------------------------------------------------------ reading

    def peek(self, decision_id: str) -> Pending | None:
        with self._lock:
            return self._by_id.get(decision_id)

    def waiting(self) -> list[Pending]:
        """Every decision still awaiting an answer, oldest first."""
        with self._lock:
            return [
                self._by_id[i]
                for i in self._order
                if self._by_id[i].status in ("pending", "claimed")
            ]

    def _forget(self, decision_id: str) -> None:
        """Drop a settled decision from the index, leaving a tombstone. Caller holds the lock.

        The handle stays valid for whoever holds a reference — ``await_answer`` reads the orders
        off it after this runs — so this bounds the queue's memory without invalidating a
        conversation already in progress.
        """
        pending = self._by_id.pop(decision_id, None)
        if decision_id in self._order:
            self._order.remove(decision_id)
        if pending is not None:
            self._settled[decision_id] = (
                "was already answered"
                if pending.status == "answered"
                else f"was abandoned: {pending.reason}"
            )
            while len(self._settled) > SETTLED_MEMORY:
                self._settled.popitem(last=False)


class QueueFull(RuntimeError):
    """Too many decisions already waiting; degrade rather than pile on."""


class Unanswered(RuntimeError):
    """The agent never answered within the allowed time."""


class NotClaimable(RuntimeError):
    """The decision cannot be answered — unknown, already settled, or abandoned."""
