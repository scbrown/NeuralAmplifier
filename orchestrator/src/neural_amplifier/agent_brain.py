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

from .brain import BrainError
from .contract import Orders, WorldView
from .doorbell import Doorbell
from .pending import DecisionQueue, NotClaimable, QueueFull, Unanswered

log = logging.getLogger(__name__)


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

    def decide(self, world_view: WorldView) -> Orders:
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

        try:
            return self.queue.await_answer(pending, self.timeout)
        except Unanswered as exc:
            raise BrainError(str(exc)) from exc


__all__ = ["AgentBrain", "DecisionQueue", "Doorbell", "NotClaimable"]
