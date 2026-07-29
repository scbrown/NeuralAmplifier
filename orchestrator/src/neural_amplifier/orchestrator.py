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
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from .brain import Brain, BrainError
from .contract import Orders, WorldView
from .decisions import DecisionLog, DecisionRecord, KnowledgeBlock, world_view_hash
from .fog import Redaction, redact
from .knowledge import Guard, Knowledge, Retriever, apply, retrieve, rule, summarise
from .telemetry import Emitter, Sink
from .validate import validate

if TYPE_CHECKING:  # replay imports us; keep the cycle type-only
    from .replay import WorldViewStore


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
        sinks: Sequence[Sink] = (),
        store: WorldViewStore | None = None,
        retriever: Retriever | None = None,
        guard: Guard | None = None,
    ) -> None:
        self.brain = brain
        self.log = log
        self.game_id = game_id or f"game-{uuid.uuid4().hex[:8]}"
        # Record of truth first: if a downstream exporter fails, the JSONL line
        # is already written (``docs/observability.md`` §3).
        self.telemetry = Emitter(*([log] if log is not None else []), *sinks)
        # Records carry only the hash of their input, so replay needs the
        # bytes kept somewhere. Content-addressed, so this dedupes for free.
        self.store = store
        # Quipu and Hank, both optional. Absent means a less-informed
        # decision, never a stalled turn (``knowledge.py``).
        self.retriever = retriever
        self.guard = guard

    def decide(self, world_view: WorldView) -> Result:
        started = time.monotonic()
        degrade_reason: str | None = None

        # Gate the foreign-diplomacy feed before the brain sees it. The adapter
        # is supposed to have filtered already; doing it here too is the
        # difference between a policy and a control.
        fog = redact(world_view)
        world_view = fog.world_view

        # Retrieval annotates the prompt; it cannot widen the action space.
        grounding = retrieve(self.retriever, world_view)
        if grounding.facts:
            world_view = world_view.model_copy(update={"grounding": list(grounding.facts)})

        # Store *after* gating and grounding, so the stored bytes are exactly
        # what the brain saw and the record's hash addresses them. Storing the
        # pre-grounding view would both break that addressing and let Quipu
        # drift leak into a replay that is supposed to isolate our own changes.
        if self.store is not None:
            self.store.put(world_view)

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

        # Precedence is order: engine legality first, so the guard never sees an
        # action the engine did not offer and cannot re-add one.
        legal = checked.orders(notes=orders.notes)
        ruling = rule(self.guard, legal, world_view)
        allowed = apply(legal, ruling)

        if degrade_reason is None and not allowed.choices:
            # Nothing survived — an empty turn is indistinguishable from a
            # stall, so treat it as degradation whichever gate emptied it.
            degrade_reason = (
                f"guard denied every choice ({len(ruling.stripped)} stripped)"
                if checked.kept
                else (
                    f"no legal choices (unknown={len(checked.unknown)},"
                    f" duplicates={len(checked.duplicates)})"
                )
            )

        final = self._fallback(world_view) if degrade_reason is not None else allowed

        record = self._record(
            world_view=world_view,
            orders=final,
            degrade_reason=degrade_reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=len(checked.unknown),
            fog=fog,
            knowledge=summarise(grounding, ruling, self.guard is not None),
        )
        # One emit call. Every layer is a projection of *this* object — see the
        # module docstring in ``telemetry.py`` for why that is load-bearing.
        self.telemetry.emit(record)

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
        fog: Redaction,
        knowledge: Knowledge,
    ) -> DecisionRecord:
        fairness = world_view.fairness
        return DecisionRecord(
            trace_id=world_view.traceparent(),
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
            knowledge=KnowledgeBlock(**asdict(knowledge)),
            redacted_deltas=fog.removed,
            fog_enforced=fog.enforced,
        )
