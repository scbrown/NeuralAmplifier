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


def orphaned_reason(previous: str | None, current: str) -> str:
    """Why a decision was dropped when a new game process started (na-bzd).

    An agent reads this text as a tool result and has to act on it, so it must say the one
    thing a generic "abandoned" cannot: the work is unrecoverable and nothing the agent does
    will change that. "Abandoned" alone reads to a model as *try again* — which is exactly the
    wrong move, because there is no process left to try against.

    It names both runs on purpose. An agent that answered a decision from run A and is now
    being offered run B's decisions needs to be able to tell that its board state is stale,
    not merely that one call failed.
    """
    return (
        f"the game process that raised it is gone. It came from run {previous}, and run "
        f"{current} is now posting decisions, so the process that was blocked on this one has "
        "exited — nothing can apply an answer to it, and any reasoning spent on it was spent "
        "on a board that no longer exists. Do not resubmit: collect a decision from the "
        "current run instead."
    )


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
    #: What the decision loop actually did with the submitted orders, set once the loop has
    #: run. Distinct from ``orders``, which is what the agent *asked* for: validation and the
    #: policy guard sit between the two, and an order that was stripped must not be reported
    #: back as applied. Telling an agent its choice landed when it did not is the one lie this
    #: interface must never tell — it is the difference between a model that repairs and a
    #: model that believes it already succeeded.
    outcome: dict[str, object] | None = None
    #: Directives the agent issued for this decision, attached before it submits. Held here
    #: rather than passed with the order because they are validated at *issue* time, while the
    #: model that wrote one is still in the loop and can fix it — discovering on every later
    #: turn that a plan was never expressible is far more expensive than one refusal now.
    proposed_directives: list[object] = field(default_factory=list)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _settled: threading.Event = field(default_factory=threading.Event, repr=False)

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
        # The engine run every live decision here belongs to — the last ``run_id`` seen on a
        # posted world view, or None while no adapter has ever stated one. Public because it is
        # the one piece of queue state an operator or a test needs to read directly; the queue
        # never interprets it beyond equality (see ``post``).
        self.run_id: str | None = None
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

        This is also where a **restarted game** is noticed (na-bzd). A ``POST /decide`` carrying
        a ``run_id`` the queue has not been seeing is the only evidence the orchestrator ever
        gets that the process it was serving is gone: there is no liveness probe, and there
        cannot usefully be one — the socket close is not observable from inside a sync FastAPI
        handler, and an age cap cannot tell a dead game from an agent that is legitimately
        thinking for minutes, which this project deliberately supports.

        **The limit that follows from that, stated so nobody assumes otherwise:** a game killed
        and *never restarted* leaves its decisions here, because the evidence is the next run
        arriving and there is no next run. What the fix guarantees is that they cannot be
        mistaken for the *current* game's work, which is the failure that cost real time — an
        agent answering stale decisions it had no way to identify. A queue with nothing posting
        to it at all is a state an operator can already see.
        """
        orphaned: list[Pending] = []
        try:
            with self._lock:
                # Before the depth check, not after: a dead run's decisions must not be able to
                # push the live run over the bound and degrade its first turn.
                orphaned = self._supersede(world_view.run_id)
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
        finally:
            # Outside the lock, and unconditionally. Each of these wakes a ``POST /decide``
            # worker blocked since the old process died — it takes this same lock on its way out
            # of ``await_answer``, so waking it while we hold it only makes it block again — and
            # whether *this* post succeeded has no bearing on whether the old run is over.
            for dead in orphaned:
                dead._done.set()
        return pending

    def _supersede(self, run_id: str | None) -> list[Pending]:
        """Retire every live decision when the game process changes. Caller holds the lock.

        Three cases, and the two quiet ones are the ones that had to be got right:

        * **No ``run_id`` on this world view** — do nothing at all, and do not touch
          ``self.run_id``. Absence means *cannot tell*, never *a new run*: every adapter that
          has not been upgraded is absent here, and reading that as a restart would drop the
          pendings of an adapter behaving perfectly. Cannot-tell must not be destructive. This
          is the same rule ``decision_deadline_ms`` follows for the same reason (na-t3h), one
          field over.
        * **First ``run_id`` this queue has ever seen** — adopt it and drop nothing. On a fresh
          orchestrator there is nothing to drop anyway; the case that matters is an orchestrator
          that has been serving an older adapter and now meets an upgraded one, where the live
          decisions may well belong to this very process. There is no evidence of a restart, so
          there is no licence to act like there was one.
        * **A different ``run_id``** — the process that was blocked on every live decision here
          has exited. Retire them all.

        That last step rests on one invariant worth stating: **one orchestrator serves one
        game.** It is what makes "a different run id" mean "the previous run ended" rather than
        "a second game is also talking to us". The design already depends on it elsewhere —
        ``game_id`` is per orchestrator instance and the decision log is per run — and two games
        sharing an orchestrator would be corrupting both of those long before they reached here.

        Retiring includes decisions whose own ``run_id`` is ``None``. They are older than the
        run that just ended, and one game at a time means older is also gone. This is inference
        from the invariant above, not a guess about a field we cannot read.
        """
        if run_id is None or run_id == self.run_id:
            return []
        previous, self.run_id = self.run_id, run_id
        if previous is None:
            return []
        orphaned: list[Pending] = []
        for decision_id in list(self._order):
            dead = self._by_id[decision_id]
            if dead.status not in ("pending", "claimed"):
                continue
            dead.status = "abandoned"
            dead.reason = orphaned_reason(previous, run_id)
            # Forgotten, so `waiting()` stops listing it and `claim()` stops handing it out in
            # the same instant it stops being answerable. Those three surfaces disagreeing is
            # the whole of what na-bzd measured: the queue, /agent/waiting and /agent/next all
            # offered work from a process that had been dead for twenty minutes.
            self._forget(decision_id)
            orphaned.append(dead)
        return orphaned

    def await_answer(
        self, pending: Pending, timeout: float | None, reason: str | None = None
    ) -> Orders:
        """Block until this decision is answered, then return the orders.

        ``timeout=None`` waits forever, which is the configured default: a turn-based game
        pausing at a decision point is what it does for a human player too. The parameter exists
        because an unattended run needs an escape — a hung agent would otherwise hang the game
        with no way back to the deterministic tier — and one number is a cheap way to have both.

        ``reason`` overrides what the abandonment is recorded and reported as. The caller
        sometimes knows *why* this particular number was the deadline — chiefly
        ``AgentBrain``, which bounds the wait on the engine's own deadline (na-t3h) — and
        "no answer within 2.25s" does not tell an agent that the game has moved on without it.
        That text is what the agent reads back as a 409, so it is the whole of what it has to
        act on.

        Raises :class:`Unanswered` on timeout, having marked the decision abandoned so a late
        answer is rejected rather than applied to a turn that has moved on.
        """
        if not pending._done.wait(timeout):
            with self._lock:
                if pending.status in ("pending", "claimed"):
                    pending.status = "abandoned"
                    pending.reason = reason or f"no answer within {timeout}s"
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
                    f"{decision_id} {why}"
                    if why
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

    def publish(self, pending: Pending, outcome: dict[str, object]) -> None:
        """Record what the decision loop did, and release anyone waiting to hear."""
        pending.outcome = outcome
        pending._settled.set()

    def await_outcome(self, pending: Pending, timeout: float) -> dict[str, object] | None:
        """Wait briefly for the decision loop to report back. ``None`` if it has not.

        Bounded and short: the loop runs immediately after the answer is handed over, so this
        is microseconds in practice. The timeout is there so a submit call can never become the
        thing that hangs — an agent left waiting on its own tool result cannot even retry.
        """
        return pending.outcome if pending._settled.wait(timeout) else None

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
