"""The decision loop: world view in, orders out, record written.

Every path through :meth:`Orchestrator.decide` emits exactly one
:class:`DecisionRecord`. That is what makes coverage a measurement rather than
an aspiration, and it is why degradation is handled here rather than at the
call site — a fallback that isn't recorded is the failure mode
``docs/observability.md`` §5.4 exists to catch.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from .brain import Brain, BrainError
from .contract import Choice, Directive, Orders, WorldView
from .decisions import (
    DecisionLog,
    DecisionRecord,
    KnowledgeBlock,
    PlanBlock,
    Tokens,
    world_view_hash,
)
from .deferrals import DEFER_ACTION_ID, Deferral, DeferralSet, is_defer
from .directives import DirectiveStore, accept, entities_shown, evaluate, relevant, tradeoffs
from .fairness import profile as fairness_profile
from .fog import Redaction, redact
from .grounding_evidence import Cache
from .knowledge import (
    Grounding,
    Guard,
    Knowledge,
    Retriever,
    Ruling,
    apply,
    grounded_ids,
    retrieve,
    rule,
    summarise,
)
from .policy import SurfacePolicy
from .queued import QueuedAnswer, QueueStore
from .telemetry import Emitter, Sink
from .turnplan import PlanEntry, PlanStore
from .turnplan import offered as plan_offered

# One implementation of "read a field the contract keeps in pydantic extras". Private to
# `turns`, imported rather than copied: its docstring documents a real pydantic asymmetry
# (an absent extra raises AttributeError instead of returning None) that a second copy would
# eventually get wrong on exactly the hand-written fixtures where it already bites.
from .turns import _extra
from .validate import validate

if TYPE_CHECKING:  # replay imports us; keep the cycle type-only
    from .replay import WorldViewStore


@dataclass
class Result:
    """Orders, plus the record that was written for them."""

    orders: Orders
    record: DecisionRecord


def _repair_attempts_from_env() -> int:
    """``NA_REPAIR_ATTEMPTS``, clamped to the 0..2 the design allows.

    Clamped rather than trusted: an unbounded repair loop is a game that never takes a turn,
    and the one thing a bound like this must not do is be configurable into uselessness.
    """
    raw = os.environ.get("NA_REPAIR_ATTEMPTS", "").strip()
    if not raw:
        return 1
    try:
        return max(0, min(2, int(raw)))
    except ValueError:
        return 1


def _why_nothing_survived(checked: object, ruling: object) -> list[str]:
    """One sentence explaining an empty result, for the brain and for the record.

    Shared deliberately: the text handed back for repair and the text written to
    ``degrade_reason`` must not drift, or a run's log will explain a failure differently from
    the way the brain was told about it.
    """
    if getattr(checked, "kept", None):
        return [f"guard denied every choice ({len(ruling.stripped)} stripped)"]  # type: ignore[attr-defined]
    return [
        f"no legal choices (unknown={len(checked.unknown)},"  # type: ignore[attr-defined]
        f" duplicates={len(checked.duplicates)})"  # type: ignore[attr-defined]
    ]


def _defer_requested(orders: Orders) -> bool:
    """Did the brain ask to come back to this?

    ANY choice naming `defer` defers the whole decision, rather than only a mixed answer or only
    a lone one. A brain that returns `[defer, unit:3]` has said two different things about one
    decision and there is no reading of that where applying the concrete half is safe: door 1
    consumes one item id, so "build a Former AND think about it" is not expressible. Deferring is
    the conservative half — it applies the engine's own pick, which is what the adapter would
    have done anyway.
    """
    return any(is_defer(choice.action_id) for choice in orders.choices)


def _defer_reason(orders: Orders) -> str | None:
    """Why the agent wants longer, taken from the deferring choice itself.

    Carried through to the adapter's record so a deferral in the game's own log says something
    more useful than that it happened.
    """
    for choice in orders.choices:
        if is_defer(choice.action_id) and choice.reason:
            return choice.reason
    return None


def _deferral_id(world_view: WorldView) -> str:
    """Stable within a decision, distinct across decisions.

    The traceparent when there is one: it is the id the adapter, the record and the agent already
    share, so a deferral is greppable against the run's other artefacts without a join table.
    Falls back to the surface and base, which is what identifies a decision to a human when the
    adapter did not stamp a trace — and to a uuid when even that is absent, because two unrelated
    deferrals colliding on the empty string would silently replace one another.
    """
    trace = world_view.traceparent()
    if trace:
        return trace
    surface = world_view.surface_id or "?"
    base_id = _extra(world_view, "base_id", int)
    if base_id is not None:
        return f"{surface}:{world_view.turn}:{base_id}"
    return f"{surface}:{world_view.turn}:{uuid.uuid4().hex[:8]}"


def _native_choice(world_view: WorldView) -> str | None:
    """What the engine will apply while the agent thinks — when it told us.

    `None` is the EXPECTED answer on `base.production` and is not a gap to fix. The adapter
    withholds its own pick from the `/decide` body on that surface deliberately (na-glk), so that
    the brain cannot anchor on it; the record gets it, the request does not. A deferral there is
    therefore honest about not knowing what stood, which is better than guessing.

    Guessing is specifically what `fallback_action_id()` would have done here, and it is a
    different concept wearing a similar name: the orchestrator's own degradation target — end the
    turn, else the first legal action. On this fixture that is `unit:0` where the engine's pick is
    `unit:1`, so recording it would have put a confident wrong answer in the one field an expired
    deferral uses to say what actually got built.

    Stringified because the field is not typed on the contract and arrives as whatever the surface
    uses: an int item id on base.production, `"se:none"` on faction.se.
    """
    raw = getattr(world_view, "native_choice", None)
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return str(int(raw))
    return str(raw) or None


def _action_name(world_view: WorldView, action_id: str | None) -> str | None:
    """The human-readable name of an action id, from the action space that offered it.

    So an expired deferral can say what actually got built rather than `facility:6`.
    """
    if action_id is None:
        return None
    for action in world_view.action_space or []:
        if getattr(action, "id", None) == action_id:
            return getattr(action, "action", None)
    return None


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
        repair_attempts: int | None = None,
        policy: SurfacePolicy | None = None,
        deferrals: DeferralSet | None = None,
        queue: QueueStore | None = None,
        turn_plan: PlanStore | None = None,
        grounding_cache: Cache | None = None,
    ) -> None:
        self.brain = brain
        # How many times a decision whose every choice was thrown out may be re-asked with the
        # reason attached. ``knowledge-architecture.md`` allows up to two.
        #
        # Default one, deliberately below that ceiling: with an agent brain the game is BLOCKED
        # for each attempt, so a repair is not a cheap retry — it is another round trip while a
        # turn sits still. One catches the overwhelmingly common case, which is a single
        # correctable mistake. Raise it with NA_REPAIR_ATTEMPTS where a run can afford to.
        self.repair_attempts = (
            repair_attempts if repair_attempts is not None else _repair_attempts_from_env()
        )
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
        # Where turn-boundary grounding evidence is published for Yupana to verify
        # (``grounding_evidence.py``). ``None`` — the default — publishes NOTHING.
        #
        # That default is deliberate and was chosen after measuring the alternative. Defaulting
        # to the real cache made the *test suite* write live evidence: 968 tests ran and two
        # files landed in ``~/.local/state/hank/grounding``, entities and all. Evidence written
        # by a test is byte-indistinguishable from evidence written by a game, so a fixture
        # could make a real agent's action read as grounded — a fabricated-grounding channel
        # inside the mechanism built to make grounding falsifiable.
        #
        # So the library never writes to a shared host path on its own. The deployment wires a
        # cache in explicitly (``service.py``), which is the one place that knows it is a real
        # run. Absent a cache the decision simply carries no reference, and Yupana reports
        # ``missing`` — the truth.
        self.grounding_cache = grounding_cache
        self.guard = guard
        # The standing plan. Absent means every decision is made on its own, which is where
        # this project started and is still a legitimate way to run.
        self.plan = plan
        # Which surfaces the LLM tier owns (``na.toml``). Absent means nobody has
        # expressed an opinion, which is how this behaved before the file existed.
        self.policy = policy or SurfacePolicy()
        # Where an agent's "ask me later" is parked. Absent means deferral is not offered, and a
        # brain that returns `defer` anyway is answered exactly as it was before this existed:
        # `defer` is in no action space, so validation reports it as an id the engine did not
        # offer and the decision degrades. That is the right behaviour for a configuration with
        # nowhere to put the deferral — the alternative is accepting responsibility for coming
        # back on behalf of a component that cannot.
        self.deferrals = deferrals
        # Standing answers with invalidation predicates. Absent means every decision is asked
        # afresh, which is how this behaved before the queue existed and is still a legitimate
        # way to run.
        self.queue = queue
        # The bulk-turn plan table (na-7bk). Absent means no bulk mode, which is the same
        # legitimate way to run as an absent queue.
        self.turn_plan = turn_plan

    def review(self, world_view: WorldView) -> Result:
        """Reconsider faction strategy without pretending a review is a game action.

        A review has no action space: its output is a revision of the standing directives.  It
        deliberately shares the brain and directive compiler with ordinary decisions, while
        bypassing action validation because there is no engine action to validate.  This is the
        second reason to wake described in ``docs/long-horizon-play.md``.

        The caller supplies the turn-boundary world view (including trajectory).  Every current
        directive is shown: relevance filtering is for a concrete decision, while the subject of
        a strategic review is the plan itself.
        """
        started = time.monotonic()
        if world_view.action_space:
            raise ValueError("a strategic review has no action space")
        if world_view.scope != "turn":
            raise ValueError("a strategic review is faction-scoped (scope='turn')")

        current = self.plan.in_force(world_view.turn) if self.plan is not None else []
        world_view = world_view.model_copy(
            update={
                "surface_id": "faction.strategy_review",
                "directives": evaluate(current, world_view) or None,
                "advisories": [
                    *(world_view.advisories or []),
                    "STRATEGIC REVIEW: there is no game action to choose. Return choices=[] and "
                    "use directives to open, keep, or revise measurable commitments for future "
                    "turns. Re-issue a commitment with the same id to record an explicit keep. "
                    "Every directive must set horizon_turn later than this review turn; a plan "
                    "without a future checkpoint cannot fail and will be refused.",
                ],
            }
        )
        if self.store is not None:
            self.store.put(world_view)

        degrade_reason: str | None = None
        try:
            answer = self.brain.decide(world_view)
        except BrainError as exc:
            degrade_reason = str(exc) or "brain error"
            answer = Orders(degraded=True)
        except Exception as exc:
            degrade_reason = f"{type(exc).__name__}: {exc}"
            answer = Orders(degraded=True)

        # Choices on a no-action occasion are neither applied nor silently reinterpreted.
        rejected = []
        if answer.choices:
            rejected.append(
                f"{len(answer.choices)} action choice(s) ignored: strategic review has no action space"
            )
        candidates = [
            directive
            for directive in answer.directives
            if directive.horizon_turn is not None and directive.horizon_turn > world_view.turn
        ]
        for directive in answer.directives:
            if directive not in candidates:
                rejected.append(
                    f"directive {directive.id!r} rejected: a strategic commitment needs "
                    f"horizon_turn later than review turn {world_view.turn}"
                )
        issued, refused = self._issue(candidates, world_view, degrade_reason)
        rejected.extend(refused)
        final = answer.model_copy(
            update={
                "choices": [],
                "directives": candidates,
                "degraded": degrade_reason is not None,
            }
        )
        record = self._record(
            world_view=world_view,
            orders=final,
            degrade_reason=degrade_reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=0,
            fog=Redaction(world_view=world_view, removed=0, enforced=True),
            knowledge=Knowledge(
                quipu_absent=self.retriever is None,
                hank_absent=self.guard is None,
            ),
            plan=self._plan_block(
                world_view,
                final,
                issued=issued,
                rejected=rejected,
                configured=self.plan is not None,
                dropped=[],
            ),
            tier="review",
        )
        self.telemetry.emit(record)
        return Result(orders=final, record=record)

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

        # Policy gate. After the world view is complete so a switched-off surface still writes a
        # full record — grounding, plan, fairness — and before the brain so it costs nothing.
        #
        # Emitting the record rather than returning early is the point: a disabled surface and
        # a surface nothing ever emitted have to be tellable apart, and only a record does that.
        # It is NOT degraded. Degraded means the brain was asked and could not answer; this one
        # was never asked, and putting a deliberate configuration into `degrade_rate` would
        # corrupt the single number that catches a run where the brain was silently absent.
        #
        # Ahead of the repair loop below, not inside it: a surface the policy does not own is
        # never asked once, so there is nothing to repair.
        if not self.policy.allows(world_view.surface_id):
            return self._deterministic(world_view, fog, grounding, dropped, started)

        # The bulk-turn plan, if the agent installed one for exactly this turn (na-7bk).
        #
        # Before the queued answers, deliberately: where both name the same decision, the plan
        # entry is the fresher claim — written against this very turn's forecast — while a queued
        # answer stands across turns on predicates. And before the brain for the same reason the
        # queue is: answering from the table without waking anyone is the entire point.
        if self.turn_plan is not None:
            planned = self.turn_plan.find(world_view)
            if planned is not None:
                if plan_offered(planned, world_view):
                    return self._planned(world_view, fog, grounding, dropped, started, planned)
                # The planned action left the space between the forecast and the ask — built
                # already, or its prerequisite lost. Retired so it cannot fail again this turn,
                # and named in front of whoever answers instead: an agent re-raised with no
                # explanation would plan the same answer into the next table.
                # `miss_why`, not `why`: the repair loop below binds `why` to a list in this
                # same function scope, and shadowing it here is the closure-repointing shape
                # that slice 2's queue/DecisionQueue bug already demonstrated.
                miss_why = f"{planned.action_id} is no longer in the action space"
                faction_id = _extra(world_view, "faction_id", int)
                if faction_id is not None:
                    self.turn_plan.miss(faction_id, planned, miss_why)
                world_view = world_view.model_copy(
                    update={
                        "advisories": [
                            *(world_view.advisories or []),
                            f"your plan entry for this decision missed: {miss_why}",
                        ]
                    }
                )

        # A standing answer, if the agent left one and the board still agrees with it.
        #
        # After grounding and the fog gate, because the predicates are measured against THIS
        # world view and it has to be the finished one. Before the brain, because not waking the
        # brain is the entire purpose — a queued answer that still cost a round trip would be a
        # slower way to get the same answer.
        if self.queue is not None:
            standing = self.queue.find(world_view)
            if standing is not None:
                holds, violated = standing.check(world_view)
                if holds and not standing.offered(world_view):
                    # Its action is gone from the space — built already, or its prerequisite
                    # lost. Not a predicate failure and not an error: the answer simply no
                    # longer names anything the engine is offering.
                    holds, violated = (
                        False,
                        [f"{standing.action_id} is no longer in the action space"],
                    )
                if holds:
                    return self._queued(world_view, fog, grounding, dropped, started, standing)
                # Overtaken. Retire it and put the reason in front of the brain, so the agent is
                # told what changed rather than merely asked again — being woken with no
                # explanation is how it re-queues the same answer into the next wake-up.
                self.queue.retire(standing, violated)
                world_view = world_view.model_copy(
                    update={
                        "advisories": [
                            *(world_view.advisories or []),
                            "your queued answer "
                            f"{standing.action_id} for this decision no longer holds: "
                            + "; ".join(violated),
                        ]
                    }
                )

        # Ask, check, and — where something is repairable — ask once more with the reason.
        #
        # ``knowledge-architecture.md`` specifies denied violations returned to the model for
        # bounded repair. Until now the behaviour was strip-then-degrade: correct, but it gives
        # up a turn the brain could have salvaged. That cost nothing while the only guard was
        # CitationGuard, which never denies. StateGuard does, so a legal-but-unaffordable order
        # now throws away a whole decision that one sentence of feedback would have fixed.
        #
        # Exactly one decision record comes out of this loop regardless of how many attempts it
        # took. A repair is part of one decision, not a second one — recording two would double
        # count the surface and make coverage read high while the game saw one build.
        repairs = 0
        asked = world_view
        # Accumulated across attempts, not taken from the last one. adherence_violations is
        # documented as structurally impossible, so any non-zero value is a broken invariant —
        # and a repair that silently absorbed the first attempt's illegal ids would make the one
        # measurement designed to catch that stop working. A corrected mistake is still a
        # mistake; it is just one that did not cost the turn.
        violations = 0
        # Same accumulate-don't-overwrite reasoning, for the same reason: a repair attempt that
        # stopped looping should not erase the evidence that the first attempt did.
        repeats = 0
        #: One hash per re-ask, in order. Empty when nothing was repaired, and also when no
        #: store is configured — which is why `repairs` is recorded separately: the count is
        #: what says a repair happened at all, the hashes are what make it reconstructible.
        repair_inputs: list[str] = []
        while True:
            try:
                orders = self.brain.decide(asked)
                # Before validate(), and that ordering IS the implementation. `defer` is in no
                # action space — the engine never offers it — so a moment later validation would
                # correctly call it an id the engine did not offer, strip it, find nothing left,
                # and degrade the decision. The agent would be told its answer was not applied:
                # true, and useless. It did not fail to answer, it declined to answer YET.
                #
                # Not repaired, not guarded, not validated. There is nothing to check — a
                # deferral names no action, so no rule about actions can have an opinion on it.
                if self.deferrals is not None and _defer_requested(orders):
                    return self._deferred(world_view, fog, grounding, dropped, started, orders)
            except BrainError as exc:
                degrade_reason = str(exc) or "brain error"
                orders = Orders()
            except Exception as exc:
                # A brain that raises something unexpected must still not stall the
                # game (invariant #9). Degrade, and record why.
                degrade_reason = f"{type(exc).__name__}: {exc}"
                orders = Orders()

            checked = validate(orders, asked)
            violations += len(checked.unknown)
            repeats += len(checked.duplicates)

            # Precedence is order: engine legality first, so the guard never sees an
            # action the engine did not offer and cannot re-add one.
            legal = checked.orders(notes=orders.notes, usage=orders.usage)
            ruling = rule(self.guard, legal, asked)
            allowed = apply(legal, ruling)

            if allowed.choices or degrade_reason is not None or repairs >= self.repair_attempts:
                break

            # Repairable: the brain answered, and every answer was thrown out. Tell it why and
            # let it choose again. Only ever from `world_view` — the advisories accumulate onto
            # the original rather than onto the last repair view, so a second attempt sees one
            # coherent list instead of a growing stack of near-duplicates.
            why = list(ruling.advisories) or _why_nothing_survived(checked, ruling)
            asked = world_view.model_copy(
                update={"advisories": [*(world_view.advisories or []), *why]}
            )
            repairs += 1

            # Store the augmented view too, and record its hash. The store write above happens
            # once, before this loop, so until now the only input kept was the one the *first*
            # attempt saw: the view the brain actually answered from on attempt two existed for
            # the length of one call and was then unreconstructible — `world_view_hash` does not
            # address it and nothing else held the bytes.
            #
            # Not by widening `world_view_hash`, which stays the decision's input: a replay
            # starts from the original and regenerates its own advisories, and that is the
            # point — a changed guard producing a different second prompt is a divergence worth
            # seeing, not one to paper over by replaying the recorded prompt back. These hashes
            # answer the other question, the forensic one: what did the brain read when it
            # answered this way. Content-addressed, so an unchanged advisory list costs nothing.
            if self.store is not None:
                repair_inputs.append(self.store.put(asked))

        if degrade_reason is None and not allowed.choices:
            # Nothing survived — an empty turn is indistinguishable from a
            # stall, so treat it as degradation whichever gate emptied it.
            degrade_reason = _why_nothing_survived(checked, ruling)[0]
            if repairs:
                degrade_reason = f"{degrade_reason}; {repairs} repair attempt(s) also failed"

        final = self._fallback(world_view) if degrade_reason is not None else allowed

        # A withdrawal answer is intrinsically a promise about later turns. Do not make its
        # durability depend on the model remembering to repeat that semantic fact in a second
        # field: the action space already says this choice decreases the metric, and accepting
        # it commits the native receiver to keep doing so after this dialog has gone away.
        issued = list(orders.directives)
        if (
            world_view.surface_id == "diplo.tribute"
            and any(c.action_id == "withdraw:comply" for c in final.choices)
            and not any(d.metric == "units_in_foreign_territory" for d in issued)
        ):
            issued.append(
                Directive(
                    id=f"withdraw-foreign-territory-{world_view.turn}",
                    intent="Honour the accepted demand to withdraw troops from foreign territory.",
                    metric="units_in_foreign_territory",
                    comparator="at_most",
                    target=0,
                    priority=8,
                    issued_turn=world_view.turn,
                )
            )
            final = final.model_copy(update={"directives": issued})

        # Harvest any plan this decision issued, after the choice is settled. A rejected
        # directive costs an advisory, never the decision it arrived with — the move may be
        # right even where the plan attached to it was not expressible.
        recorded, plan_rejections = self._issue(issued, world_view, degrade_reason)

        record = self._record(
            world_view=world_view,
            orders=final,
            degrade_reason=degrade_reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=violations,
            repeated=repeats,
            repairs=repairs,
            repair_inputs=repair_inputs,
            fog=fog,
            knowledge=summarise(
                grounding,
                ruling,
                self.guard is not None,
                cited=orders.cited,
                shown_by_plan=entities_shown(world_view),
            ),
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

    def _deterministic(
        self,
        world_view: WorldView,
        fog: Redaction,
        grounding: Grounding,
        dropped: list[str],
        started: float,
    ) -> Result:
        """Record a decision this configuration hands to the engine.

        Empty orders: the adapter applies its own answer when we name no action, which is what
        it did before any of this existed. The record still lands, at ``deterministic`` tier and
        explicitly not degraded.
        """
        orders = Orders()
        record = self._record(
            world_view=world_view,
            orders=orders,
            degrade_reason=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=0,
            fog=fog,
            knowledge=summarise(grounding, Ruling(), self.guard is not None),
            plan=self._plan_block(
                world_view,
                orders,
                issued=[],
                rejected=[],
                configured=self.plan is not None,
                dropped=dropped,
            ),
            tier="deterministic",
        )
        self.telemetry.emit(record)
        return Result(orders=orders, record=record)

    def _queued(
        self,
        world_view: WorldView,
        fog: Redaction,
        grounding: Grounding,
        dropped: list[str],
        started: float,
        standing: QueuedAnswer,
    ) -> Result:
        """Apply a standing answer whose predicates still hold, without asking anyone.

        Recorded at `tier="queued"` and NOT degraded. The decision was made by an agent — earlier,
        and conditionally, but made — and the conditions were checked against this board before it
        was applied. That is a stronger claim than most `llm` decisions can make, since those are
        checked against nothing after the fact.
        """
        from .contract import Choice

        standing.applied += 1
        orders = Orders(
            choices=[
                Choice(
                    action_id=standing.action_id,
                    reason=f"standing answer: {standing.reason or 'queued by the agent'}",
                )
            ]
        )
        record = self._record(
            world_view=world_view,
            orders=orders,
            degrade_reason=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=0,
            fog=fog,
            knowledge=summarise(grounding, Ruling(), self.guard is not None),
            plan=self._plan_block(
                world_view,
                orders,
                issued=[],
                rejected=[],
                configured=self.plan is not None,
                dropped=dropped,
            ),
            tier="queued",
        )
        self.telemetry.emit(record)
        return Result(orders=orders, record=record)

    def _planned(
        self,
        world_view: WorldView,
        fog: Redaction,
        grounding: Grounding,
        dropped: list[str],
        started: float,
        entry: PlanEntry,
    ) -> Result:
        """Answer from the bulk-turn table, without asking anyone (na-7bk).

        Recorded at `tier="plan"` and NOT degraded. The decision was made by an agent, for this
        exact turn, from the turn forecast — the strongest freshness claim any non-live answer
        here can make, which is why no predicate check stands between the table and the reply.
        The one gate that ran is invariant 1's: the action is still in the space the engine
        offered for THIS ask.
        """
        from .contract import Choice

        entry.applied += 1
        orders = Orders(
            choices=[
                Choice(
                    action_id=entry.action_id,
                    reason=f"turn plan: {entry.reason or 'planned by the agent'}",
                )
            ]
        )
        record = self._record(
            world_view=world_view,
            orders=orders,
            degrade_reason=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=0,
            fog=fog,
            knowledge=summarise(grounding, Ruling(), self.guard is not None),
            plan=self._plan_block(
                world_view,
                orders,
                issued=[],
                rejected=[],
                configured=self.plan is not None,
                dropped=dropped,
            ),
            tier="plan",
        )
        self.telemetry.emit(record)
        return Result(orders=orders, record=record)

    def _deferred(
        self,
        world_view: WorldView,
        fog: Redaction,
        grounding: Grounding,
        dropped: list[str],
        started: float,
        orders: Orders,
    ) -> Result:
        """The agent asked for more time. Answer the engine now; keep the decision open.

        The deferral is RELAYED on the wire rather than converted into empty orders here, and
        that is deliberate in two directions:

        - **Forwards**: the adapter records its own decision line, and `defer` is what lets it
          write `tier="deferred"` there too. Sending empty orders instead would reach it as "no
          action_id in reply" — indistinguishable from an orchestrator that failed to answer, in
          the game's own primary telemetry. The distinction this whole mechanism exists to make
          would survive on our side of the wire and be lost on theirs.
        - **Backwards**: an adapter that has never heard of deferral treats `defer` as an
          unparseable action id, which already lands on `native_choice` — the same answer, by the
          older path. So the wire change degrades compatibly against a DLL built before it.

        Either way the game is answered in the time it takes to return, which is the entire point:
        door 1 cannot be made to wait.

        `tier="deferred"` and NOT degraded: this decision was made, by an agent, and the decision
        was to come back. `degraded` marks a run that could not think; a run full of deferrals was
        thinking hard enough to want longer.
        """
        empty = Orders(
            choices=[Choice(action_id=DEFER_ACTION_ID, reason=_defer_reason(orders))],
            notes=orders.notes,
        )
        record = self._record(
            world_view=world_view,
            orders=empty,
            degrade_reason=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            unknown=0,
            fog=fog,
            knowledge=summarise(grounding, Ruling(), self.guard is not None),
            plan=self._plan_block(
                world_view,
                empty,
                issued=[],
                rejected=[],
                configured=self.plan is not None,
                dropped=dropped,
            ),
            tier="deferred",
        )
        self.telemetry.emit(record)

        # Parked only after the record exists. A deferral the agent can see but that never
        # reached the log would be an open decision with no trace of having been raised.
        assert self.deferrals is not None
        standing = _native_choice(world_view)
        self.deferrals.open(
            Deferral(
                id=_deferral_id(world_view),
                surface_id=world_view.surface_id or "",
                turn=world_view.turn,
                faction_id=_extra(world_view, "faction_id", int),
                faction=world_view.faction,
                base_id=_extra(world_view, "base_id", int),
                base=_extra(world_view, "base", str),
                standing_action_id=standing,
                standing_action=_action_name(world_view, standing),
                # The view the agent actually read, grounding included: resolving from a
                # different set of facts than the deferral was made on is how a considered
                # answer becomes a differently-uninformed one.
                world_view=world_view.model_dump(exclude_none=True),
            )
        )
        return Result(orders=empty, record=record)

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
        tier: str = "llm",
        #: Defaults to zero for the deterministic path, where it is not an omission: the brain
        #: was never asked, so there is no answer that could have repeated itself.
        repeated: int = 0,
        repairs: int = 0,
        repair_inputs: Sequence[str] = (),
    ) -> DecisionRecord:
        # Derive the ledger when the adapter stamped the *inputs* but not the entries.
        #
        # An empty `fairness_profile` is documented as the claim "won under unmodified rules"
        # (docs/observability.md), so an AI-slot decision recorded with no handicaps asserts
        # fair play on a game that had none. An adapter that knows only what the engine tells
        # it — which slot, which difficulty — would produce exactly that.
        #
        # Deriving here rather than in the adapter is also what `fairness.py` is for: three
        # entries change which side they favour as difficulty moves and two are inert under the
        # fork's defaults, so a static list stamped in C++ would declare handicaps that are not
        # in force and mislabel ones that are. The adapter reports the inputs; the rules live
        # in one place. An adapter that stamps entries itself is left alone and checked by
        # `fairness.drift` instead.
        fairness = world_view.fairness
        if (
            fairness is not None
            and fairness.slot
            and fairness.difficulty
            and not fairness.handicaps
        ):
            fairness = fairness_profile(fairness.slot, fairness.difficulty)
        # ONE hash, bound to both the record and the evidence. Hashing twice would be two
        # serialisations of the same object and nothing would notice if they ever disagreed —
        # and a mismatch here is reported by Yupana as `unresolved`, i.e. as broken evidence
        # rather than as the producer bug it would be.
        digest = world_view_hash(world_view.model_dump(mode="json"))
        return DecisionRecord(
            trace_id=world_view.traceparent(),
            game_id=self.game_id,
            turn=world_view.turn,
            year=world_view.year,
            faction=world_view.faction,
            engine=world_view.engine,
            surface_id=world_view.surface_id,
            scope=world_view.scope,
            tier=tier,  # type: ignore[arg-type]
            world_view_hash=digest,
            action_space_size=len(world_view.action_space),
            chosen=[c.model_dump(mode="json") for c in orders.choices],
            reason=orders.choices[0].reason if orders.choices else None,
            degraded=orders.degraded,
            degrade_reason=degrade_reason,
            fairness_profile=[h.id for h in fairness.handicaps] if fairness else [],
            model=getattr(self.brain, "model", None) or self.brain.name,
            # What the call was actually billed for. Absent on a scripted or degraded decision,
            # where zeroes are the truth rather than a gap (na-6db: the field existed and was
            # structurally zero on every record, which reads as a measured "free").
            tokens=Tokens(**orders.usage) if orders.usage else Tokens(),
            latency_ms=latency_ms,
            adherence_violations=unknown,
            repeated_actions=repeated,
            repairs=repairs,
            repair_inputs=list(repair_inputs),
            knowledge=KnowledgeBlock(**asdict(knowledge)),
            grounding=self._grounding_ref(world_view, digest),
            plan=plan,
            redacted_deltas=fog.removed,
            fog_enforced=fog.enforced,
        )

    def _grounding_ref(self, world_view: WorldView, digest: str) -> dict[str, str] | None:
        """Bind this turn's consultation to this decision's input, and publish the evidence.

        The consultation happened once, at ``/turn``; the binding is per decision, because a
        decision is the unit anyone audits. Content-addressing makes the fan-out free — two
        decisions with the same world view resolve to the same file — so this costs one local
        write per distinct input and never a graph round trip. That matters: ``/decide`` blocks
        the game thread and has been measured at 244 decisions in one turn (na-x5n), which is
        why the consultation is not repeated here.

        Fog-scoped through ``faction_id``. An unscoped lookup would bind one faction's decision
        to another faction's consultation and the record would look perfectly ordinary, which
        is the failure mode ``turns.view`` is fail-closed to avoid.

        Never raises. Grounding degrades and never stalls a turn — but the degradation is
        *visible*, because a decision with no reference is one Yupana reports as ``missing``
        rather than one it silently passes.
        """
        if self.grounding_cache is None:
            return None
        lookup = getattr(self.retriever, "consultation_for", None)
        if not callable(lookup):
            return None
        faction_id = getattr(world_view, "faction_id", None)
        consultation = lookup(world_view.turn, faction_id)
        if consultation is None:
            return None
        ref = self.grounding_cache.publish(consultation, digest)
        return ref.as_dict() if ref is not None else None
