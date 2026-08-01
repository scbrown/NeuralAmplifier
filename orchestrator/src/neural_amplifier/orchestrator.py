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
from .contract import Directive, Orders, WorldView
from .decisions import (
    DecisionLog,
    DecisionRecord,
    KnowledgeBlock,
    PlanBlock,
    world_view_hash,
)
from .directives import DirectiveStore, accept, entities_shown, evaluate, relevant, tradeoffs
from .fog import Redaction, redact
from .knowledge import (
    Guard,
    Knowledge,
    Retriever,
    apply,
    grounded_ids,
    retrieve,
    rule,
    summarise,
)
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
        plan: DirectiveStore | None = None,
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
        # The standing plan. Absent means every decision is made on its own, which is where
        # this project started and is still a legitimate way to run.
        self.plan = plan

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
            # Facts are injected id-first: "unit:formers Formers; terraforms terrain".
            #
            # The id has to be visible for two reasons. The brain cannot cite what it cannot
            # see, and Orders.cited is the only evidence that retrieval influenced the answer
            # rather than merely preceded it. And the citation guard reads the offered set back
            # out of this block, so injecting bare text made every citation look fabricated.
            #
            # A retriever that does not label its facts still works; the lines are then plain
            # text and utilisation is unmeasurable rather than zero.
            ids = grounding.fact_ids
            lines = (
                [f"{fid} {text}" for fid, text in zip(ids, grounding.facts, strict=True)]
                if len(ids) == len(grounding.facts)
                else list(grounding.facts)
            )
            world_view = world_view.model_copy(update={"grounding": lines})

        # Standing plan, measured against this turn, plus what each option would cost it.
        # Injected before the store for the same reason grounding is: the stored bytes must be
        # exactly what the brain saw, or a replay is not a replay.
        # The grounding ids double as this decision's entity set: they are exactly the datalinks
        # nodes retrieval matched for the options and subjects on offer, which is what a directive
        # links to. Nothing extra needs resolving.
        world_view, dropped = self._with_directives(world_view, grounding.fact_ids)

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

        # Harvest any plan this decision issued, after the choice is settled. A rejected
        # directive costs an advisory, never the decision it arrived with — the move may be
        # right even where the plan attached to it was not expressible.
        recorded, plan_rejections = self._issue(orders.directives, world_view, degrade_reason)

        record = self._record(
            world_view=world_view,
            orders=final,
            degrade_reason=degrade_reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=len(checked.unknown),
            fog=fog,
            knowledge=summarise(grounding, ruling, self.guard is not None, cited=orders.cited),
            plan=self._plan_block(
                world_view,
                orders,
                issued=recorded,
                rejected=plan_rejections,
                configured=self.plan is not None,
                dropped=dropped,
            ),
        )
        # One emit call. Every layer is a projection of *this* object — see the
        # module docstring in ``telemetry.py`` for why that is load-bearing.
        self.telemetry.emit(record)

        return Result(orders=final, record=record)

    def _with_directives(
        self, world_view: WorldView, entity_ids: Sequence[str] = ()
    ) -> tuple[WorldView, list[str]]:
        """Inject the standing plan, measured, plus what each option would cost it.

        Both halves or neither. A directive without its current value asks the model to guess
        whether it is relevant; a directive without the trade-off asks it to guess the cost of
        ignoring one. The pair is what makes "this plan is priority 7" a comparison rather than
        an assertion.

        Wrapped so a broken plan file cannot cost a turn — same rule as retrieval (invariant 9).
        """
        if self.plan is None:
            return world_view, []
        try:
            in_force = self.plan.in_force(world_view.turn)
            if not in_force:
                return world_view, []
            # Retrieved, not broadcast. A game accumulates hundreds of directives and a decision
            # is shown the handful that bear on it — see ``relevant``.
            selection = relevant(in_force, world_view, entity_ids)
            if not selection.hits:
                return world_view, selection.dropped
            return (
                world_view.model_copy(
                    update={
                        "directives": evaluate(selection.hits, world_view),
                        "tradeoffs": tradeoffs(selection.selected, world_view) or None,
                    }
                ),
                selection.dropped,
            )
        except Exception:  # noqa: BLE001 — a plan we cannot read is a less-informed decision
            return world_view, []

    def _issue(
        self, issued: list[Directive], world_view: WorldView, degraded: str | None
    ) -> tuple[list[str], list[str]]:
        """Record directives this decision placed on later ones.

        Returns the ids actually stored and one message per refusal. Nothing is accepted from a
        degraded decision: the fallback did not reason about anything, so a plan attributed to it
        would be a plan nobody made — and it would then steer every decision afterwards.
        """
        if self.plan is None or not issued:
            return [], []
        if degraded is not None:
            return [], [f"{len(issued)} directive(s) discarded: the decision itself degraded"]
        try:
            accepted, rejected = accept(issued, world_view)
            self.plan.add(accepted)
            return [d.id for d in accepted], rejected
        except Exception as exc:  # noqa: BLE001 — see _with_directives
            return [], [f"could not record directives: {type(exc).__name__}: {exc}"]

    @staticmethod
    def _plan_block(
        world_view: WorldView,
        orders: Orders,
        issued: list[str],
        rejected: list[str],
        configured: bool,
        dropped: list[str],
    ) -> PlanBlock:
        """The measurement half. See :class:`PlanBlock` for why each field is here."""
        statuses = world_view.directives or []
        in_force = [s.directive.id for s in statuses]
        offered = set(in_force)
        # A directive's entities are grounding ids, but they reach the brain through the
        # directives block. ``summarise`` filters ``cited`` against grounding alone — correctly,
        # or utilisation would stop measuring retrieval — so a citation of one of these would
        # otherwise vanish from the record entirely. Grounding wins where both offered the id;
        # there it is already counted as retrieval doing its job.
        via_plan = entities_shown(world_view) - grounded_ids(world_view)
        return PlanBlock(
            in_force=in_force,
            # Filtered against what was actually in force, exactly as ``cited`` is filtered
            # against the offered facts: an id the model invented must not inflate attention.
            followed=[d for d in dict.fromkeys(orders.followed) if d in offered],
            overrode=[d for d in dict.fromkeys(orders.overrode) if d in offered],
            entities_cited=[c for c in dict.fromkeys(orders.cited) if c in via_plan],
            unmeasurable=[s.directive.id for s in statuses if s.satisfied is None],
            unsatisfied=[s.directive.id for s in statuses if s.satisfied is False],
            issued=issued,
            rejected=rejected,
            conflicts=[
                f"{t.action_id}:{t.directive_id}"
                for t in (world_view.tradeoffs or [])
                if t.would_violate
            ],
            not_shown=dropped,
            plan_absent=not configured,
        )

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
        plan: PlanBlock,
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
            plan=plan,
            redacted_deltas=fog.removed,
            fog_enforced=fog.enforced,
        )
