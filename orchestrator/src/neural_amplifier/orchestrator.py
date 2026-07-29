"""The decision loop: world view in, orders out, record written.

Every path through :meth:`Orchestrator.decide` emits exactly one
:class:`DecisionRecord`. That is what makes coverage a measurement rather than
an aspiration, and it is why degradation is handled here rather than at the
call site — a fallback that isn't recorded is the failure mode
``docs/observability.md`` §5.4 exists to catch.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from .brain import Brain, BrainError
from .contract import Orders, WorldView
from .decisions import DecisionLog, DecisionRecord, world_view_hash
from .validate import validate


@dataclass
class Result:
    """Orders, plus the record that was written for them."""

    orders: Orders
    record: DecisionRecord


class Orchestrator:
    """Drives one brain, writes one decision log."""

    def __init__(
        self,
        brain: Brain,
        log: DecisionLog | None = None,
        game_id: str | None = None,
    ) -> None:
        self.brain = brain
        self.log = log
        self.game_id = game_id or f"game-{uuid.uuid4().hex[:8]}"

    def decide(self, world_view: WorldView) -> Result:
        started = time.monotonic()
        degrade_reason: str | None = None

        try:
            orders = self.brain.decide(world_view)
        except BrainError as exc:
            degrade_reason = str(exc) or "brain error"
            orders = Orders()
        except Exception as exc:
            # A brain that raises something unexpected must still not stall the
            # game (invariant #9). Degrade, and record why.
            degrade_reason = f"{type(exc).__name__}: {exc}"
            orders = Orders()

        checked = validate(orders, world_view)

        if degrade_reason is None and not checked.kept:
            # The brain replied but nothing survived validation — an empty turn
            # is indistinguishable from a stall, so treat it as degradation.
            degrade_reason = (
                f"no legal choices (unknown={len(checked.unknown)},"
                f" duplicates={len(checked.duplicates)})"
            )

        if degrade_reason is not None:
            final = self._fallback(world_view)
        else:
            final = checked.orders(notes=orders.notes)

        record = self._record(
            world_view=world_view,
            orders=final,
            degrade_reason=degrade_reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=len(checked.unknown),
        )
        if self.log is not None:
            self.log.write(record)

        return Result(orders=final, record=record)

    def _fallback(self, world_view: WorldView) -> Orders:
        """The safe default: end the turn where possible, else do nothing.

        The game never stalls waiting on the brain — but the orders are marked
        ``degraded`` so the run is not mistaken for a real one.
        """
        action_id = world_view.fallback_action_id()
        if action_id is None:
            return Orders(choices=[], degraded=True)
        from .contract import Choice

        return Orders(
            choices=[Choice(action_id=action_id, reason="safe fallback")],
            degraded=True,
        )

    def _record(
        self,
        world_view: WorldView,
        orders: Orders,
        degrade_reason: str | None,
        latency_ms: int,
        unknown: int,
    ) -> DecisionRecord:
        fairness = world_view.fairness
        return DecisionRecord(
            trace_id=world_view.trace.traceparent if world_view.trace else None,
            game_id=self.game_id,
            turn=world_view.turn,
            year=world_view.year,
            faction=world_view.faction,
            engine=world_view.engine,
            surface_id=world_view.surface_id,
            scope=world_view.scope,
            tier="llm",
            world_view_hash=world_view_hash(world_view.model_dump(mode="json")),
            action_space_size=len(world_view.action_space),
            chosen=[c.model_dump(mode="json") for c in orders.choices],
            reason=orders.choices[0].reason if orders.choices else None,
            degraded=orders.degraded,
            degrade_reason=degrade_reason,
            fairness_profile=[h.id for h in fairness.handicaps] if fairness else [],
            model=getattr(self.brain, "model", None) or self.brain.name,
            latency_ms=latency_ms,
            adherence_violations=unknown,
        )
