"""A brain that is an external agent — Claude Code, or any MCP client.

The pivot, and it is one class. :class:`~.orchestrator.Orchestrator` reaches a model on exactly
one line; every invariant it protects — fog gating, grounding, directive trade-offs, action-space
validation, the policy guard, the decision record — lives around that line. So swapping the model
for an agent in a terminal does not need a second pipeline. It needs a
:class:`~.brain.Brain` that parks the decision on a queue, rings a doorbell, and waits.

What the agent receives is therefore the *fully grounded* world view, not the raw one: retrieval,
directives and trade-offs are injected before the brain is called, and the stored bytes are the
ones it saw. What it sends back goes through ``validate`` and the guard exactly like a model's
answer would. An agent cannot name an action the engine did not offer, because the thing that
stops a model doing that is not in the model.

Blocking is the configured default and is a deliberate relaxation of invariant 9. A turn-based
game pausing at a decision point is what it already does for a human player, and this brain is
for the case where a human or an agent *is* playing. ``NA_AGENT_TIMEOUT`` is the escape hatch an
unattended run needs — set it and a silent agent degrades to the deterministic tier instead of
hanging the game.
"""

from __future__ import annotations

import logging
import os
import threading

from .brain import BrainError
from .contract import Orders, WorldView
from .doorbell import Doorbell
from .pending import DecisionQueue, NotClaimable, QueueFull, Unanswered

log = logging.getLogger(__name__)

#: How far ahead of the engine's own deadline this brain gives up, in seconds.
#:
#: The two deadlines must not be raced. If the orchestrator abandons at the same instant the
#: engine does, the outcome depends on scheduling and network latency, and the losing case is
#: precisely the one na-t3h is about: an answer accepted into a decision loop for a turn the
#: game has already resolved. Shaving a margin makes the orchestrator lose that race
#: deliberately and always, which is the only way it can be reasoned about.
#:
#: 250ms rather than something smaller because the shave has to cover the reply's trip back
#: through ``/decide`` to the adapter, not just the local wakeup — the engine's clock starts at
#: its send and stops at its receive.
ABANDON_MARGIN_SECONDS = 0.25


def _timeout_from_env() -> float | None:
    """``NA_AGENT_TIMEOUT`` in seconds; unset or ``0`` means wait forever.

    Zero maps to "no limit" rather than "give up instantly" because the value a person types
    when they mean *no timeout* is 0 far more often than they mean *time out immediately*, and
    the second reading turns every decision into an instant fallback with nothing in the log
    that looks like a misconfiguration.
    """
    raw = os.environ.get("NA_AGENT_TIMEOUT", "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        log.warning("NA_AGENT_TIMEOUT=%r is not a number; waiting without a limit", raw)
        return None
    return seconds if seconds > 0 else None


class AgentBrain:
    """Hands each decision to an attached agent and blocks until it answers.

    Holds no model, no prompt and no API key. Everything it knows how to do is wait, which is
    why the class is small and why the agent-facing surface (:mod:`.mcp_server`) can be a thin
    client of the same queue rather than a parallel implementation of the decision loop.
    """

    name = "agent"

    def __init__(
        self,
        queue: DecisionQueue | None = None,
        doorbell: Doorbell | None = None,
        timeout: float | None = -1.0,
    ) -> None:
        self.queue = queue if queue is not None else DecisionQueue()
        self.doorbell = doorbell if doorbell is not None else Doorbell.from_env()
        # -1 is the "not specified" marker rather than None, because None is a *meaningful*
        # value here — it is the documented way to say "wait forever" — and a default of None
        # would make the env var unreachable for anyone constructing this directly.
        self.timeout = _timeout_from_env() if timeout == -1.0 else timeout
        # The pending decision this thread is currently blocked on. Thread-local because one
        # FastAPI worker handles one decision at a time, and it is how the service hands the
        # loop's *outcome* back to the agent that submitted it — the agent needs to learn that
        # the guard stripped its choice, and only the caller of decide() ever sees that.
        self._current = threading.local()

    def decide(self, world_view: WorldView) -> Orders:
        # Being called again on this thread means the decision loop is repairing: the last
        # answer was thrown out by validation or the guard and it is asking once more with the
        # reason attached. Tell whoever submitted that answer, before they are left waiting on
        # an outcome that will never come — their submit call is blocked on it right now, and a
        # silent timeout reads as "the orchestrator broke" rather than "choose again".
        superseded = getattr(self._current, "pending", None)
        if superseded is not None:
            self.queue.publish(
                superseded,
                {
                    "applied": [],
                    "status": (
                        "NOT applied — a repair decision follows; collect it and choose again"
                    ),
                    "advisories": list(world_view.advisories or []),
                    "degraded": False,
                },
            )
            self._current.pending = None

        try:
            pending = self.queue.post(world_view)
        except QueueFull as exc:
            # Every pending decision is one blocked HTTP worker, so the game cannot outrun
            # itself and a full queue means the agent side stopped consuming. Piling on would
            # convert one stuck decision into all of them.
            raise BrainError(f"decision queue full: {exc}") from exc

        rang = self.doorbell.ring(pending.id, world_view.surface_id)
        if not rang:
            # Not an error. A polling agent needs no doorbell, and this is the only place that
            # distinction is visible, so it is worth a line in the log rather than silence.
            log.info("no doorbell for %s; waiting for an agent to poll", pending.id)

        self._current.pending = pending
        timeout, reason = self._deadline(world_view)
        try:
            return self.queue.await_answer(pending, timeout, reason=reason)
        except Unanswered as exc:
            raise BrainError(str(exc)) from exc

    def _deadline(self, world_view: WorldView) -> tuple[float | None, str | None]:
        """How long to wait for this decision, and what to say if the wait runs out.

        The configured timeout (``NA_AGENT_TIMEOUT``) says how long *we* are willing to wait.
        ``world_view.decision_deadline_ms`` says how long the *game* will still be listening.
        The tighter of the two is the only one worth waiting for: past the engine's deadline the
        adapter has applied its own pick and moved on, so an answer arriving after it cannot
        reach the game no matter how correct it is (na-t3h — measured, 66 adapter rows, zero
        ``tier=llm``, against orchestrator records claiming applied llm decisions).

        Neither present keeps the old behaviour exactly: wait on whatever was configured,
        including forever. An adapter that does not state a deadline has not told us it has one,
        and inventing a bound here would degrade decisions the game was still waiting for.
        """
        engine = world_view.decision_deadline_seconds()
        if engine is None:
            return self.timeout, None
        # Shave the margin, but never below half the engine's own allowance. A fixed subtraction
        # goes negative on a very tight deadline, and a negative wait abandons *instantly* —
        # turning a merely-difficult deadline into a decision the agent was never offered a
        # chance at. Proportional shaving keeps the wait positive for any positive deadline.
        bounded = max(engine - ABANDON_MARGIN_SECONDS, engine / 2)
        reason = (
            f"the engine's {world_view.decision_deadline_ms}ms deadline passed with no answer; "
            "the game has applied its own fallback and moved on. Do not resubmit this decision "
            "— re-read the board and answer the next one."
        )
        if self.timeout is None or self.timeout > bounded:
            return bounded, reason
        # The configured timeout is the tighter one, so it is what actually expires — say so,
        # rather than blaming a deadline that had not been reached.
        return self.timeout, None

    def publish_outcome(self, outcome: dict[str, object]) -> None:
        """Tell the agent what the decision loop actually did with its orders.

        Called by the service once ``Orchestrator.decide`` has returned, so the agent's
        ``submit_orders`` result reports what ran rather than what was asked for. Silent when
        this thread was not driving an agent decision — a scripted or Claude run reaches the
        same code path and has nobody to tell.
        """
        pending = getattr(self._current, "pending", None)
        if pending is not None:
            self.queue.publish(pending, outcome)
            self._current.pending = None


__all__ = ["AgentBrain", "DecisionQueue", "Doorbell", "NotClaimable"]
