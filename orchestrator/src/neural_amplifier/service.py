"""The HTTP surface: ``POST /decide``.

One request per decision point, exactly as ``docs/contract.md`` describes.
Keeping it plain JSON over HTTP is what makes every decision inspectable,
loggable, and replayable — and it is what step 7 of the observability plan
(replay as regression) spends.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from .agent_brain import AgentBrain
from .brain import Brain, ClaudeBrain, ScriptedBrain
from .config import Config
from .config import load as load_config
from .contract import Choice, Directive, Orders, WorldView
from .coverage import report
from .decisions import DecisionLog
from .deferrals import DeferralSet
from .directives import DirectiveStore, accept
from .intents import IntentError, UnitIntent
from .intents import validate as validate_intent
from .memory import memory_scope
from .orchestrator import Orchestrator
from .orders import OrderChannel, build_command
from .outcomes import EngineOutcome, OutcomeStore
from .pending import NotClaimable, Pending
from .queued import Comparator, Predicate, QueuedAnswer, QueueError, QueueStore
from .replay import WorldViewStore
from .telemetry import OtelSink, Sink
from .turnplan import PlanEntry, PlanError, PlanStore, TurnPlan
from .turnplan import validate as validate_plan
from .turns import TurnAnnouncement, TurnStore

log = logging.getLogger(__name__)


def build_brain(config: Config | None = None) -> Brain:
    """Scripted by default; every other brain is opt-in.

    Tests and CI must never make a paid API call by accident, so the real brain requires
    ``kind = "claude"`` in ``na.toml`` or ``NA_BRAIN=claude`` explicitly.

    ``kind = "agent"`` (or ``NA_BRAIN=agent``) hands decisions to an attached MCP client —
    Claude Code in a tmux pane, or anything else that speaks the tool surface. It makes no API
    call of its own, but it does *block* until something answers, so it is opt-in for a
    different reason: a service started with it by accident would look hung rather than idle.

    Both switches read through ``config``, which already folds ``NA_BRAIN`` into
    ``brain.kind``. Reading the environment again here would give the env var two meanings that
    could disagree, with the file silently losing on one path and winning on the other.
    """
    cfg = (config or load_config()).brain
    if cfg.kind == "agent":
        return AgentBrain()
    if cfg.kind != "claude":
        return ScriptedBrain()
    kwargs: dict[str, str] = {}
    if cfg.model:
        kwargs["model"] = cfg.model
    if cfg.effort:
        kwargs["effort"] = cfg.effort
    return ClaudeBrain(**kwargs)


def _parse_intent(raw: object, command: str) -> tuple[UnitIntent, int] | None:
    """Validate an intent while the agent still holds the order — BEFORE anything is issued.

    Split from the write half deliberately. Validation and recording want opposite moments: an
    unacceptable intent must be refused before the order goes out (a 422 after the game confirmed
    a move would throw away the confirmation the caller needs to see), while the write must wait
    for that confirmation (a remembered reason for an order that never landed describes a plan no
    unit is executing). One function doing both forced validation to the wrong side of `issue()`,
    and in a batch it would fire mid-flight, after some orders had already gone.

    Faction comes from the CALLER, never inferred from the unit id. Units are numbered in one
    engine-wide sequence, so inferring would be a coincidence away from writing one faction's
    private plan into another's graph, which is the fog boundary this all turns on.
    """
    if not isinstance(raw, dict):
        return None

    faction_id = raw.get("faction_id")
    if not isinstance(faction_id, int | float | str) or faction_id == "":
        raise HTTPException(422, "intent.faction_id is required — it is the graph the plan goes in")

    triggers = []
    for item in raw.get("triggers") or []:
        if not isinstance(item, dict):
            raise HTTPException(422, "each intent trigger must be an object")
        target = item.get("target")
        if item.get("metric") in (None, "") or not isinstance(target, int | float | str):
            raise HTTPException(422, "an intent trigger needs a metric and a numeric target")
        triggers.append(
            Predicate(
                metric=str(item["metric"]),
                comparator=cast(Comparator, str(item.get("comparator", "at_least"))),
                target=float(target),
            )
        )

    parts = command.split()
    intent = UnitIntent(
        unit_id=_as_int(raw.get("unit_id", parts[1] if len(parts) > 1 else ""), "intent.unit_id"),
        goal=str(raw.get("goal") or command),
        rationale=str(raw["rationale"]) if raw.get("rationale") else None,
        until_turn=_as_opt_int(raw.get("until_turn"), "intent.until_turn"),
        triggers=triggers,
    )
    try:
        validate_intent(intent)
    except IntentError as exc:
        # 422 while the agent is still holding the decision. An intent nothing could bring back
        # for review is worse than none: it reads in every later prompt as a plan under review.
        raise HTTPException(422, str(exc)) from exc
    return intent, _as_int(faction_id, "intent.faction_id")


def _write_intent(
    orchestrator: Orchestrator, intent: UnitIntent, faction_id: int
) -> dict[str, object]:
    """Write the WHY behind a confirmed long-horizon order into the ordering faction's graph.

    Attached to the order rather than posted separately, deliberately: the reason and the order
    are one act, and a second endpoint would make it possible to have either without the other —
    an unexplained goto, or a remembered plan no unit is carrying out.
    """
    store = getattr(getattr(orchestrator, "retriever", None), "store", None)
    game_id = getattr(getattr(orchestrator, "retriever", None), "game_id", None)
    if store is None or not game_id:
        return {
            "recorded": False,
            "why": "no memory store is bound, so there is nowhere to remember it",
            "intent": intent.summary(),
        }
    store.write_intent(intent, memory_scope(str(game_id), faction_id))
    return {"recorded": True, "intent": intent.summary()}


def _as_int(value: object, field: str) -> int:
    """A JSON body is `object`-valued, so a number off the wire needs narrowing somewhere.

    Here rather than inline, so a 422 names the field the caller got wrong instead of surfacing a
    bare ValueError from several call sites.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise HTTPException(422, f"{field} must be a number")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"{field} must be a number: {exc}") from exc


def _as_opt_int(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    return _as_int(value, field)


def _expire_deferrals(deferrals: DeferralSet, turn: int | None) -> list[str]:
    """Close deferrals the turn has moved past. Returns the ids, for logging."""
    if turn is None:
        return []
    return [d.id for d in deferrals.expire_before(turn)]


def _resolve_deferrals_for(
    deferrals: DeferralSet, command: str, result: dict[str, object]
) -> list[str]:
    """Link a door-2 order back to the deferral it answers.

    The agent resolves a parked build by issuing `build <base_id> <item_id>` — the verb that
    already exists — rather than by calling a second endpoint meaning the same thing. So the link
    has to be reconstructed from the order's own arguments, and `base_id` is the term both sides
    hold.

    Only on an order the game CONFIRMED. `OrderChannel` reports `unknown` when it cannot tell,
    and `unknown` is a real outcome that is never upgraded to "applied" on silence (`/order`'s
    own docstring). Closing a deferral on an unconfirmed order would retire the agent's
    outstanding work on the strength of a message that may never have been read.
    """
    if str(result.get("status")) != "ok":
        return []
    parts = command.split()
    if len(parts) < 2 or parts[0] != "build":
        return []
    try:
        base_id = int(parts[1])
    except ValueError:
        return []
    closed = deferrals.resolve_for_base(base_id, resolution=command)
    return [d.id for d in closed]


def _build_retriever(config: Config) -> object | None:
    """Quipu-backed grounding, opt-in via ``knowledge.quipu_url``.

    Absent means an ungrounded decision, never a failed start — the
    knowledge layer is an optimisation (``knowledge.py``).
    """
    inner: object | None = None
    if config.knowledge.quipu_url:
        from .datalinks import QuipuRetriever

        inner = QuipuRetriever(
            config.knowledge.quipu_url,
            engine=config.knowledge.engine,
            token_budget=config.knowledge.token_budget,
        )

    # Learned memory (K3) wraps whatever grounding there is, including none. A run with no
    # rulebook retrieval still benefits from what earlier games taught — arguably more, because
    # the brain has less else to go on — so this is gated on its own variable rather than on
    # `inner`.
    if os.environ.get("NA_MEMORY_QUIPU_URL"):
        from .memory import RememberingRetriever

        return RememberingRetriever(inner)
    return inner


def build_guard(retriever: object | None, config: Config | None = None) -> object | None:
    """The policy guards: state preconditions always, citation integrity whenever grounding is.

    Public because ``Orchestrator`` takes the guard as an argument and does not default it, so
    every caller that builds an orchestrator itself decides independently whether to instrument
    it — and the stability harness silently decided "no" for its entire life, recording
    ``hank_absent`` on every measured decision. That is indistinguishable in a record from Hank
    being down. One function, so "a fully instrumented orchestrator" means the same thing
    everywhere.

    Not separately opt-in: citation integrity is meaningless without retrieval, and
    meaningful the moment there is any. It needs no external service and never denies, so the
    cost of having it on is one set comparison per decision and the benefit is that a
    fabricated or unread citation stops being invisible.

    Set ``knowledge.guard = false`` (or NA_HANK_GUARD=0) to disable.

    **The board guard is the third, and it is the one with a service behind it.** With
    ``NA_YUPANA_URL`` set, ``yupana.YupanaGuard`` joins the chain and evaluates graph-pattern
    policies over a copy-on-write overlay of this faction's board — the questions StateGuard
    structurally cannot answer, because they are about entities and relations rather than
    arithmetic on declared figures. It is added last: the two local guards need no service and
    must not be made to depend on one being up. Absent ``NA_YUPANA_URL`` nothing changes, and
    a configured-but-unreachable yupana degrades rather than denying (``yupana.py``).
    """
    # Deliberately not gated on ``retriever is None``: that would take StateGuard off with it,
    # and the body below turns on the two guards for different reasons.
    if not (config or load_config()).knowledge.guard:
        return None
    from .hank import CitationGuard, GuardChain, StateGuard

    # StateGuard runs with or without retrieval. Citation integrity is meaningless without
    # facts to cite, but "can this order actually be paid for out of current state" is a
    # question every decision has, grounded or not — and it is the one the agent-brain pivot
    # made urgent, because an agent holds beliefs across a whole game where a model call held
    # none. Gating it on the retriever would have left it off in exactly the ungrounded runs
    # where the model has least to check itself against.
    guards: list[object] = [StateGuard()]
    if retriever is not None:
        guards.append(CitationGuard())
    # Last, and only when configured. A board guard is a network call inside the decision
    # loop, so it goes behind the two that are not — an unreachable yupana then costs a
    # degraded advisory rather than the affordability check that runs for free.
    if os.environ.get("NA_YUPANA_URL"):
        from .yupana import YupanaGuard

        guards.append(YupanaGuard(policies=load_policies()))
    return GuardChain(*guards)


def load_policies() -> list[dict[str, object]]:
    """Board policies for the yupana guard, from ``NA_YUPANA_POLICIES``.

    A file path, holding a JSON list of yupana ``StatePolicy`` objects. Read per process rather
    than compiled in, because these are governance: they are authored in Quipu and projected,
    and a copy baked into this repository would enforce yesterday's rules while looking current.

    An unreadable or malformed file yields **no policies**, loudly in the log and not as an
    exception. The guard then evaluates nothing and says so — every decision carries "policy not
    evaluated" advisories rather than a clean board, which is the honest reading and is exactly
    the distinction yupana's own ``unevaluated`` exists to preserve.

    A relative path is tried against the working directory first and then against the repository
    root. That is not tidiness: ``just play`` runs the service with ``--directory orchestrator``,
    so the obvious ``NA_YUPANA_POLICIES=policies/board.example.json`` typed at the repo root
    would otherwise resolve one level too deep and silently guard with nothing.

    **Quipu wins when it is configured.** ``NA_POLICY_QUIPU_URL`` points at a store holding
    ``aegis:Policy`` nodes, which is where these are meant to live: a rule with provenance, a
    history and one owner, rather than a file somebody edited. The JSON path stays as the
    fallback for a run with no store — and it is a *fallback*, not a merge, because two live
    sources of governance is the drift this was supposed to remove.
    """
    quipu_url = os.environ.get("NA_POLICY_QUIPU_URL")
    if quipu_url:
        projected = _policies_from_quipu(quipu_url)
        if projected is not None:
            return projected
        # Falling through to the file, loudly. A store that is configured and unreachable is an
        # operator problem, and guarding with a stale file while saying nothing is how a run
        # ends up enforcing rules nobody can point at.
        log.warning("policy store %s unreachable; falling back to NA_YUPANA_POLICIES", quipu_url)

    setting = os.environ.get("NA_YUPANA_POLICIES")
    if not setting:
        return []
    candidates = [Path(setting)]
    if not candidates[0].is_absolute():
        candidates.append(Path(__file__).resolve().parents[3] / setting)
    path = next((p for p in candidates if p.is_file()), candidates[0])
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("yupana policies unreadable at %s (%s); guarding with none", path, exc)
        return []
    if not isinstance(loaded, list):
        log.warning("yupana policies at %s is not a list; guarding with none", path)
        return []
    return [p for p in loaded if isinstance(p, dict)]


def _policies_from_quipu(url: str) -> list[dict[str, object]] | None:
    """Board policies projected from a Quipu store, or ``None`` if it could not be read.

    ``None`` and ``[]`` are different answers and the caller treats them differently: an empty
    projection is a store that genuinely holds no order-boundary policy, which is a legitimate
    configuration, while ``None`` is "I could not ask" and falls back to the file.
    """
    from .datalinks import QuipuRetriever
    from .yupana import POLICY_QUERY, policies_from_quipu

    try:
        rows = QuipuRetriever(url).query(POLICY_QUERY)
    except Exception as exc:  # a policy store is not worth failing a service start over
        log.warning("could not project policies from %s: %s", url, exc)
        return None
    projected = policies_from_quipu(rows)
    log.info("projected %d board policy(ies) from %s", len(projected), url)
    return projected


def _accepted_status(pending: Pending) -> str:
    """What to tell an agent whose choice survived validation and the guard.

    This used to be the flat string "applied to the game", and that string was a lie the
    orchestrator was structurally unable to detect (na-t3h). It is inferred from the
    orchestrator's *own* outcome — the orders it is about to return — and the orchestrator does
    not observe the game. Between this line and anything happening on the board sit an HTTP
    response, the adapter's own legality gates, and the engine, which can drop a choice for
    reasons nobody has encoded (observability.md §5.5.1 measured exactly that). The only
    evidence of application lives on the adapter side, in `na-observations.jsonl`.

    So the wording says what this process did and stops there. It is deliberately still
    unambiguous about success, because the alternative failure — an agent that reads a hedge as
    a rejection and re-submits — costs a turn too.

    The deadline branch is the specific case na-t3h found, and it is a *backstop*, not the
    primary fix: `AgentBrain` now abandons before the engine does, so a late answer is normally
    refused with 409 and never reaches this code. It can still be reached when the shave was too
    thin — the decision loop runs grounding, the guard and possibly a repair after the answer is
    handed over, and that work is not inside `ABANDON_MARGIN_SECONDS`. An answer that was in
    time and an outcome that is late look identical from here except for this check.
    """
    deadline = pending.world_view.decision_deadline_seconds()
    if deadline is not None and pending.age_seconds() >= deadline:
        return (
            "NOT applied — the engine's "
            f"{pending.world_view.decision_deadline_ms}ms deadline passed before this decision "
            "finished; the game has applied its own fallback and moved on"
        )
    return "accepted — returned to the engine to apply"


def create_app(
    brain: Brain | None = None,
    log: DecisionLog | None = None,
    sinks: Sequence[Sink] | None = None,
) -> FastAPI:
    app = FastAPI(title="Neural Amplifier orchestrator", version="0.1.0")

    # Read once, here. A malformed na.toml should refuse to start the service rather than
    # failing one turn at a time in a running game.
    config = load_config()

    resolved_log = log
    if resolved_log is None and config.run.decision_log:
        resolved_log = DecisionLog(Path(config.run.decision_log))

    # Layer 2 is opt-in and loud: NA_OTEL=1 without the extra installed raises
    # at startup rather than serving a run with no live view (§3).
    resolved_sinks = list(sinks) if sinks is not None else ([OtelSink()] if config.run.otel else [])

    # Without this a run's log references inputs nobody kept, and replay
    # (observability step 7) has nothing to feed back.
    store_path = config.run.world_view_store
    resolved_retriever = _build_retriever(config)
    # The standing plan. `NA_PLAN`/`run.plan` was read into config and consumed by nothing, so a
    # served orchestrator always ran with `plan=None`: `/agent/directive` answered "attached; it
    # takes effect when you submit this decision", the decision succeeded, and the directive went
    # nowhere. Every record from a real game carried `plan_absent: true`, which reads as "no plan
    # was configured" rather than "the configuration was ignored" (na-43h).
    #
    # Measured before the fix, turn 45: an agent issued `bank-for-expansion` on a live
    # base.production decision, got the success response with a stamped baseline of 181, and the
    # plan file was never created. The next decision was shown nothing.
    #
    # Absent path stays absent — no `NA_PLAN` means no store, which is a legitimate way to run and
    # is what `plan_absent` is for.
    plan_path = config.run.plan
    # Where a deferred decision waits for the agent to come back to it (na-7bk). Always built:
    # a deferral costs nothing until a brain returns `defer`, and a configuration where the
    # mechanism silently is not there is one where an agent's considered "later" degrades into
    # "the brain got it wrong" with no way to tell from the record.
    deferrals = DeferralSet()
    app.state.deferrals = deferrals

    # Standing answers with invalidation predicates (na-7bk slice 2).
    #
    # NOT called `queue`: further down, `queue` is rebound to the AgentBrain's DecisionQueue, and
    # since both live in this one function scope the endpoints below would close over whatever
    # that name ended up meaning — the decision queue, not this. Caught by mypy rather than by a
    # test, because the tests exercising these endpoints use a non-agent brain and the rebinding
    # never happens for them. It would have failed only against a real attached agent.
    answer_queue = QueueStore()
    app.state.answer_queue = answer_queue

    # The bulk-turn plan table (na-7bk). Same naming caution as `answer_queue` above — `plan`
    # already means the DirectiveStore in this scope, and `plan_path` its configuration, so this
    # is `turn_plan` everywhere or the endpoints close over the wrong object.
    turn_plan = PlanStore()
    app.state.turn_plan = turn_plan

    orchestrator = Orchestrator(
        brain=brain or build_brain(config),
        log=resolved_log,
        sinks=resolved_sinks,
        store=WorldViewStore(store_path) if store_path else None,
        retriever=resolved_retriever,  # type: ignore[arg-type]
        guard=build_guard(resolved_retriever, config),  # type: ignore[arg-type]
        plan=DirectiveStore(Path(plan_path)) if plan_path else None,
        policy=config.surfaces,
        deferrals=deferrals,
        queue=answer_queue,
        turn_plan=turn_plan,
    )
    app.state.orchestrator = orchestrator

    # The memory retriever needs the game id to build a faction scope, and the Orchestrator is
    # what mints it — so binding happens here, after construction, rather than at build time.
    # Until it is bound the retriever recalls NOTHING (fail-closed): an unscoped read is the leak
    # the scope exists to prevent, so "we do not know which game" must not become "read all of
    # them" (na-7bk).
    bind = getattr(resolved_retriever, "bind_game", None)
    if callable(bind):
        bind(orchestrator.game_id)

    # Engine-side outcomes (outcomes.py). Distinct from the brain's `publish_outcome`, which
    # reports what the ORCHESTRATOR applied; this is what the ENGINE did with it afterwards.
    outcome_store = OutcomeStore()
    app.state.outcomes = outcome_store

    # The agent's own initiative (orders.py). Unconfigured is a legitimate way to run: the
    # endpoint reports itself unavailable rather than the service refusing to start, because
    # ordering is an addition to a run and not a precondition for one.
    order_channel = OrderChannel(config.run.game_dir)
    app.state.orders = order_channel

    # The turn as a whole (turns.py). Fed from three places — the adapter's announcement, every
    # world view that arrives, and every outcome — so it stays true without anyone maintaining it.
    turn_store = TurnStore()
    app.state.turns = turn_store

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "brain": orchestrator.brain.name,
            "game_id": orchestrator.game_id,
            # A run whose exporter is quietly failing otherwise looks identical
            # to a healthy one.
            "telemetry": {
                "sinks": [type(s).__name__ for s in orchestrator.telemetry.sinks],
                "healthy": orchestrator.telemetry.healthy,
                "failures": [f.error for f in orchestrator.telemetry.failures[-3:]],
            },
        }

    @app.post("/decide", response_model=Orders)
    def decide(world_view: WorldView) -> Orders:
        # Before the decision, so a world view that takes a long time to answer already shows as
        # raised rather than looking like one that never arrived.
        # A turn moving is what expires a deferral, and this is the earliest place the
        # orchestrator learns it moved. Not tidy-up: by the time the engine is asking about turn
        # N+1 it has already played turn N's build, so an answer arriving now would land on the
        # NEXT turn's minerals. That is a different decision, not a late answer to this one, and
        # recording it as a resolution would be a lie about which board it was made on.
        _expire_deferrals(deferrals, world_view.turn)
        turn_store.note_raised(world_view)
        result = orchestrator.decide(world_view)
        turn_store.note_answered(world_view)
        # Report back to whoever answered this, if anyone did. The agent asked for one thing;
        # validation and the policy guard sit between that and what ran, and an agent told its
        # stripped choice was "applied" has no reason to repair it.
        publish = getattr(orchestrator.brain, "publish_outcome", None)
        if publish is not None:
            publish(
                {
                    # Deliberately no "status": the submit handler computes that from what was
                    # applied. Only the repair path sets one, because only it knows something
                    # the applied list cannot convey.
                    "applied": [c.action_id for c in result.orders.choices],
                    "degraded": result.record.degraded,
                    "degrade_reason": result.record.degrade_reason,
                    "advisories": list(result.record.knowledge.advisories),
                }
            )
        return result.orders

    # ---------------------------------------------------------------- agent side
    #
    # Present only when the brain is an AgentBrain. Mounting them unconditionally would
    # advertise a queue nothing is filling, and an agent that connects to a scripted run
    # would sit at `next_decision` forever with no way to tell why.
    if isinstance(orchestrator.brain, AgentBrain):
        queue = orchestrator.brain.queue

        @app.post("/agent/next")
        def agent_next(body: dict[str, object] | None = None) -> dict[str, object]:
            """Claim the oldest decision waiting for an answer.

            Returns the world view the brain would have seen — grounded, with directives and
            trade-offs already injected — because that assembly happens before the brain is
            called and this *is* the brain.
            """
            # A JSON body is `object`-valued, so the wait is whatever the caller sent. A
            # non-numeric one means "do not block" rather than a 500: this endpoint is the
            # agent's only way to collect work, and failing it over a malformed optional
            # costs more than ignoring the field.
            #
            # Two spellings, because the field has two names in the wild and only one of them
            # ever worked. The wire name is `wait`; the MCP tool's parameter is `wait_seconds`
            # (mcp_server.next_decision), and the skill doc's examples show only the MCP name.
            # So a raw-HTTP caller copying the name it had been shown sent a field this
            # endpoint had never heard of, and the leniency above swallowed it: an instant
            # empty poll, indistinguishable from "no decisions are waiting".
            #
            # Measured 2026-08-21 (na-c1d): {"wait_seconds": 8} returned in 0s, {"wait": 8}
            # blocked the full 8s. The cost was not a confusing API — it was a delegated
            # decision loop burning through its 30-empty-polls exit condition in 68 seconds
            # and abandoning a game that was blocked, waiting, and visible in this very queue.
            body = body or {}
            raw_wait: object = 0
            for name in ("wait", "wait_seconds"):
                if body.get(name) is not None:
                    raw_wait = body[name] or 0
                    break
            wait = float(raw_wait) if isinstance(raw_wait, int | float | str) else 0.0
            pending = queue.claim(wait=min(wait, 110.0))

            # The other half of the same bug, and the half that generalises: silence. An
            # unknown key is still not a 422 — the reasoning above has not changed, and a
            # caller sending one extra field should not lose its claim over it — but it is no
            # longer SILENT. A caller that guesses a field name now gets the name back, which
            # is the difference between a poll it can debug and a queue it believes is empty.
            ignored = sorted(set(body) - {"wait", "wait_seconds"})
            extra: dict[str, object] = {"ignored_fields": ignored} if ignored else {}

            if pending is None:
                return {"decision_id": None, "waiting": 0, **extra}
            return {
                "decision_id": pending.id,
                "surface_id": pending.world_view.surface_id,
                "world_view": pending.world_view.model_dump(exclude_none=True),
                **extra,
            }

        @app.post("/agent/directive")
        def agent_directive(body: dict[str, object]) -> dict[str, object]:
            """Attach a standing directive to a decision, before answering it.

            Validated here rather than at submit time, and that is the whole point: a directive
            naming a metric outside the vocabulary is refused while the agent is still holding
            it and can rewrite it. The alternative — accepting it and discovering on every later
            turn that it cannot be evaluated — reads in a record as compliance rather than as a
            gap.
            """
            decision_id = str(body.get("decision_id") or "")
            pending = queue.peek(decision_id)
            if pending is None:
                raise HTTPException(409, f"no open decision {decision_id!r} to attach a plan to")
            try:
                directive = Directive.model_validate(
                    {k: v for k, v in body.items() if k != "decision_id"}
                )
            except ValidationError as exc:
                raise HTTPException(422, f"not a well-formed directive: {exc}") from exc

            accepted, rejected = accept([directive], pending.world_view)
            if rejected:
                raise HTTPException(422, rejected[0])
            pending.proposed_directives.extend(accepted)
            return {
                "decision_id": decision_id,
                "issued": accepted[0].id,
                "baseline": accepted[0].baseline,
                "status": "attached; it takes effect when you submit this decision",
            }

        @app.post("/agent/submit")
        def agent_submit(body: dict[str, object]) -> dict[str, object]:
            """Answer a claimed decision.

            The orders are handed to the waiting ``AgentBrain.decide``, which returns them into
            the ordinary decision loop — so validation, the policy guard and the decision record
            all run exactly as they do for a model. Nothing here is a shortcut around them.
            """
            decision_id = str(body.get("decision_id") or "")
            action_id = str(body.get("action_id") or "")
            reason = body.get("reason")
            if not decision_id or not action_id:
                raise HTTPException(422, "decision_id and action_id are both required")

            pending = queue.peek(decision_id)
            if pending is not None:
                # Checked here as well as downstream so the *agent* is told, in the tool result
                # it is already reading, that it named something the engine never offered. The
                # orchestrator would strip it either way, but silently — and a model cannot
                # correct a mistake nobody reported to it.
                legal = pending.world_view.action_ids()
                if action_id not in legal:
                    raise HTTPException(
                        422,
                        f"{action_id!r} is not in the action space for {decision_id}. "
                        f"Legal ids: {sorted(legal)}",
                    )

            def _ids(key: str) -> list[str]:
                raw = body.get(key) or []
                return [str(x) for x in raw] if isinstance(raw, list) else []

            try:
                answered = queue.answer(
                    decision_id,
                    Orders(
                        choices=[Choice(action_id=action_id, reason=str(reason or "") or None)],
                        # The three measurement channels an agent must be able to reach. Without
                        # them an agent-driven run silently reports zero grounding utilisation
                        # and zero directive attention — indistinguishable in a record from a
                        # model that read the facts and the plan and ignored both.
                        cited=_ids("cited"),
                        followed=_ids("followed"),
                        overrode=_ids("overrode"),
                        directives=list(pending.proposed_directives) if pending else [],
                    ),
                )
            except NotClaimable as exc:
                raise HTTPException(409, str(exc)) from exc

            # Wait for the loop to say what it actually did. Short, because it runs immediately
            # after the answer is handed over — and bounded, because a submit call that hangs
            # leaves an agent unable even to retry.
            outcome = queue.await_outcome(answered, timeout=10.0)
            if outcome is None:
                return {
                    "decision_id": answered.id,
                    "submitted": action_id,
                    "status": "submitted; the outcome did not arrive in time to report",
                }
            raw_applied = outcome.get("applied") or []
            applied = [str(item) for item in raw_applied] if isinstance(raw_applied, list) else []
            # A status set by the publisher wins. It knows things this handler cannot infer
            # from `applied` alone — chiefly that a repair is on its way, which looks identical
            # to a terminal failure if you only read an empty list.
            status = str(outcome.get("status") or "")
            if not status:
                if action_id in applied:
                    status = _accepted_status(answered)
                elif applied:
                    status = f"NOT applied — the guard replaced it with {', '.join(applied)}"
                else:
                    status = "NOT applied — nothing survived validation and the guard"
            return {
                "decision_id": answered.id,
                "submitted": action_id,
                "applied": applied,
                "status": status,
                "degraded": outcome.get("degraded"),
                "degrade_reason": outcome.get("degrade_reason"),
                "advisories": outcome.get("advisories") or [],
            }

        @app.post("/agent/outcomes")
        def agent_outcomes(body: dict[str, object] | None = None) -> dict[str, object]:
            """Did the orders I gave actually happen?

            An agent knows what it submitted and what the orchestrator applied. Neither answers
            whether the ENGINE kept it — see outcomes.py. This is the only way to find out, and
            an unreported decision comes back `unknown` rather than being assumed fine.
            """
            raw = (body or {}).get("cursor", 0) or 0
            cursor = int(raw) if isinstance(raw, int | float | str) and str(raw).isdigit() else 0
            raw_limit = (body or {}).get("limit", 50) or 50
            limit = int(raw_limit) if isinstance(raw_limit, int | float) else 50
            high, fresh = outcome_store.since(cursor=cursor, limit=min(limit, 200))
            return {
                "cursor": high,
                "outcomes": [o.model_dump(exclude_none=True) for o in fresh],
                "stats": outcome_store.stats(),
            }

        @app.post("/agent/turn")
        def agent_turn(body: dict[str, object] | None = None) -> dict[str, object]:
            """The whole turn, for an agent deciding where to spend a limited pool.

            `{"faction_id": 3}`, optionally with `{"turn": 103}` for a turn other than the
            current one.

            `next_decision` gives you one decision at a time in the engine's order. This is the
            same turn seen whole — including decisions that have not been raised yet, so a build
            choice can be made knowing what else is competing for the same minerals. It is the
            forecast `submit_turn_plan` is written from: read it, decide the turn, install the
            table.

            **`faction_id` is required, and that is the fog gate rather than a formality.** The
            turn store holds every faction's slots together, each carrying its base's name, so
            an unscoped read here would hand an agent the other five factions' bases — the same
            information cheat the adapter refuses when it builds a world view, arriving through
            a door nobody was watching. Measured on this store before it was closed: 49
            University base names reachable from a read made for the Gaians.

            A default would be the hole. Every caller that forgot the argument would read every
            faction and look exactly like one that meant to, which is precisely the failure the
            slice-1 ruling made `recall()` fail-closed to prevent. `GET /turn` remains the
            unscoped OBSERVER read; it is a different route because it is a different job.
            """
            raw_faction = (body or {}).get("faction_id")
            if raw_faction in (None, ""):
                raise HTTPException(
                    422,
                    "faction_id is required — the turn view is faction-scoped, and an "
                    "unscoped read would show you the other factions' bases",
                )
            faction_id = _as_int(raw_faction, "faction_id")
            raw = (body or {}).get("turn")
            turn = int(raw) if isinstance(raw, int) else None
            return turn_view_payload(turn, faction_id=faction_id)

        @app.post("/agent/waiting")
        def agent_waiting() -> dict[str, object]:
            return {
                "waiting": [
                    {
                        "decision_id": p.id,
                        "surface_id": p.world_view.surface_id,
                        "turn": p.world_view.turn,
                        "status": p.status,
                        "age_seconds": round(p.age_seconds(), 1),
                    }
                    for p in queue.waiting()
                ]
            }

        @app.post("/agent/whatif")
        def agent_whatif(body: dict[str, object]) -> dict[str, object]:
            """Speculatively apply one offered action and report what it reaches.

            Read-only on the game and on the queue: it neither answers the decision nor claims
            it, so an agent can ask about three options and then submit a fourth. Yupana applies
            the order to a copy-on-write overlay and commits nothing.

            Never fails the request for want of a board. An unconfigured or unreachable yupana
            comes back as ``unavailable`` with the reason, the same shape as an unknown action
            id — this is a convenience mid-decision, and a 500 here would teach an agent to stop
            asking rather than to read the answer.
            """
            decision_id = str(body.get("decision_id") or "")
            action_id = str(body.get("action_id") or "")
            pending = queue.peek(decision_id)
            if pending is None:
                raise HTTPException(status_code=404, detail=f"no decision {decision_id!r}")

            from .yupana import YupanaGuard, what_if

            guard = YupanaGuard(policies=load_policies(), game_id=orchestrator.game_id)
            return {
                "decision_id": decision_id,
                "action_id": action_id,
                "whatif": what_if(guard, pending.world_view, action_id),
            }

    # ---------------------------------------------------------------- engine outcomes
    #
    # Mounted in EVERY mode, unlike /agent/*. This is the adapter reporting what the engine did,
    # which has nothing to do with which brain answered — a scripted run's divergences are worth
    # exactly as much as an agent's, and gating this on AgentBrain would mean the measurement
    # lanes (decision_stability.py, the eval harness) could never see them.
    @app.post("/outcome")
    def outcome(body: EngineOutcome) -> dict[str, object]:
        """The adapter reporting what the engine actually did with an order.

        Returns the sequence number rather than a bare ack so a caller can tell a recorded report
        from a dropped one. `traceparent` is the correlation key — see outcomes.py for why it is
        that and not a decision id.
        """
        seq = outcome_store.record(body)
        # Fold it onto the turn view too, so "what happened to everything I ordered this turn" is
        # one read rather than a join the caller has to do.
        turn_store.note_outcome(body)
        return {"recorded": seq, "traceparent": body.traceparent}

    @app.get("/outcomes")
    def outcomes(cursor: int = 0, limit: int = 100) -> dict[str, object]:
        """Everything reported since `cursor`, with the cursor to use next.

        The cursor advances even when nothing is returned, so a poller cannot get wedged
        re-reading a tail it has already seen.
        """
        high, fresh = outcome_store.since(cursor=cursor, limit=limit)
        return {
            "cursor": high,
            "outcomes": [o.model_dump(exclude_none=True) for o in fresh],
            "stats": outcome_store.stats(),
        }

    @app.get("/outcome/{traceparent}")
    def outcome_for(traceparent: str) -> dict[str, object]:
        """What is known about one decision.

        `unknown` when nothing has been reported — deliberately a 200 and not a 404, because a
        404 invites a caller to treat "no answer yet" as "nothing went wrong".
        """
        return outcome_store.get(traceparent).model_dump(exclude_none=True)

    # ---------------------------------------------------------------- the turn as a whole
    #
    # `/agent/next` can only ever offer the oldest single decision, because when base #1 is asked
    # the rest of the turn has not been POSTed and does not exist to the queue. These let the
    # adapter say what is coming, so an agent can spend a limited pool across the whole turn.
    @app.post("/turn")
    def announce_turn(body: TurnAnnouncement) -> dict[str, object]:
        """The adapter forecasting what the coming turn will ask about.

        Re-announcing a turn replaces it: a second announcement means the adapter reached the
        between-turns seam again, so the earlier forecast describes a board that no longer exists.
        """
        count = turn_store.announce(body)
        return {"turn": body.turn, "expected": count}

    @app.get("/turn")
    def turn_view(turn: int | None = None) -> dict[str, object]:
        """The turn as it stands: every decision, its status, and what has not arrived.

        **The observer's read, and cross-faction on purpose.** The harness, the turn report and
        replay all need the whole round; that lane is the record of what happened, never an
        input to a decision. An AGENT wants `POST /agent/turn`, which requires a faction and
        shows only that faction's slots — see the fog note there for what an unscoped read
        would leak and why the gate lives at the API layer rather than in the store.

        `expected` and `raised` are deliberately different words. The announcement is a FORECAST
        made from the previous turn's board — a base can be captured or finish a project, and the
        decision it was expected to raise never comes. So a turn where 51 were forecast and 47
        arrived is ordinary, not an error. It is also what a stuck adapter looks like, which is
        why `unraised` names the missing ones rather than leaving you to infer them from a count.
        """
        return turn_view_payload(turn)

    def turn_view_payload(turn: int | None, faction_id: int | None = None) -> dict[str, object]:
        return turn_store.view(turn, faction_id=faction_id).model_dump(exclude_none=True)

    # ---------------------------------------------------------------- orders (door 2)
    #
    # Issuing, as opposed to answering. Mounted in every mode for the same reason /outcome is:
    # this is about the adapter and the game, not about which brain is attached.
    @app.post("/order")
    def order(body: dict[str, object]) -> dict[str, object]:
        """Command a unit or base directly, without waiting for the engine to ask.

        `{"verb": "move", "args": [12, 40, 21]}` or `{"command": "move 12 40 21"}`, or a batch:
        `{"orders": [{"verb": "move", "args": [12, 40, 21]}, ...]}`. An order — single or a batch
        entry — may carry an `intent` (na-7bk slice 3): the WHY behind a long-horizon order,
        written into the ordering faction's graph, but only once the game confirms that order
        individually.

        Whether the order is *legal* is the engine's question and is asked there — the adapter
        wraps the engine's own functions and its own validators. What this refuses is a shape the
        adapter could only reject anyway.

        A `status` of `unknown` means exactly that: the order may or may not have happened. It is
        never reported as applied on the strength of not having heard otherwise.
        """
        # A batch: [{"verb": ..., "args": [...], "intent": {...}?}, ...] or ["move 1 2 3", ...]
        raw_orders = body.get("orders")
        if isinstance(raw_orders, list) and raw_orders:
            if isinstance(body.get("intent"), dict):
                raise HTTPException(
                    422,
                    "in a batch, attach each intent to its own order entry — "
                    "a body-level intent names no order",
                )
            lines: list[str] = []
            intents: list[tuple[UnitIntent, int] | None] = []
            for entry in raw_orders:
                if isinstance(entry, str):
                    # A blank line would be silently dropped by the channel, and every result
                    # after it would then be matched to the wrong order. Refused, not filtered:
                    # the caller built this list and can fix it; we cannot re-align it.
                    if not entry.strip():
                        raise HTTPException(
                            422, "an empty order line in a batch would desync every later result"
                        )
                    lines.append(entry.strip())
                    intents.append(None)
                    continue
                if not isinstance(entry, dict):
                    raise HTTPException(422, "each order must be a string or {verb, args}")
                try:
                    lines.append(
                        build_command(
                            str(entry.get("verb") or ""), [int(a) for a in entry.get("args") or []]
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise HTTPException(422, str(exc)) from exc
                # Validated NOW, before anything is issued: a bad intent must refuse the whole
                # batch while the agent still holds it, not surface after half the army moved.
                intents.append(_parse_intent(entry.get("intent"), lines[-1]))
            raw_timeout_b = body.get("timeout_s")
            timeout_b = float(raw_timeout_b) if isinstance(raw_timeout_b, int | float) else None
            batch = order_channel.issue_batch(lines, timeout_s=timeout_b)
            envelope = batch.as_dict()

            # Per-order confirmation is POSITIONAL, and fail-closed. The adapter answers a batch
            # with one entry per order it ran, in order; an order with no entry — dropped past
            # the per-tick cap, or an old adapter that read only the first line — is unconfirmed,
            # and unconfirmed is never upgraded to "applied". The envelope's own `ok` is not
            # consulted for this: it says whether EVERYTHING worked, not which things did.
            confirmed = [
                i < len(batch.results) and bool(batch.results[i].get("ok"))
                for i in range(len(lines))
            ]
            notes: list[dict[str, object]] = []
            for i, parsed in enumerate(intents):
                if parsed is None:
                    continue
                intent, intent_faction = parsed
                note: dict[str, object] = (
                    _write_intent(orchestrator, intent, intent_faction)
                    if confirmed[i]
                    else {
                        "recorded": False,
                        "why": "this order was not individually confirmed, "
                        "so no intent was written",
                    }
                )
                notes.append({"order": i, "command": lines[i], **note})
            if notes:
                envelope["intents"] = notes

            # A confirmed build in a batch retires its deferral exactly as a single one does —
            # sweeping the pending set is the batch's main use, so this path not resolving would
            # leave /agent/pending offering work the agent already did.
            resolved_b: list[str] = []
            for i, line in enumerate(lines):
                if confirmed[i]:
                    resolved_b += _resolve_deferrals_for(deferrals, line, {"status": "ok"})
            if resolved_b:
                envelope["resolved_deferrals"] = resolved_b
            return envelope

        raw_command = body.get("command")
        if isinstance(raw_command, str) and raw_command.strip():
            command = raw_command.strip()
        else:
            verb = str(body.get("verb") or "")
            raw_args = body.get("args") or []
            if not isinstance(raw_args, list):
                raise HTTPException(422, "args must be a list of integers")
            try:
                args = [int(a) for a in raw_args]
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, f"args must be integers: {exc}") from exc
            try:
                command = build_command(verb, args)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

        # An intent, if the caller attached one (na-7bk slice 3) — validated BEFORE the order is
        # issued, so a refusal reaches the agent while it still holds the whole act, and a
        # confirmed order's outcome is never thrown away by a 422 about its annotation.
        parsed_intent = _parse_intent(body.get("intent"), command)

        raw_timeout = body.get("timeout_s")
        timeout_s = float(raw_timeout) if isinstance(raw_timeout, int | float) else None
        outcome = order_channel.issue(command, timeout_s=timeout_s).as_dict()

        # Recorded only on an order the game CONFIRMED, for the same reason a deferral is only
        # resolved on one: the engine keeps the goto, and a remembered reason for an order that
        # never landed would describe a plan no unit is executing.
        if parsed_intent is not None:
            intent, intent_faction = parsed_intent
            intent_note: dict[str, object] = (
                _write_intent(orchestrator, intent, intent_faction)
                if str(outcome.get("status")) == "ok"
                else {
                    "recorded": False,
                    "why": "the order was not confirmed, so no intent was written",
                }
            )
            outcome = {**outcome, "intent": intent_note}
        # A build that lands closes whatever deferral it answers. Reported back in the response
        # rather than only recorded, so an agent sweeping its pending set learns the parked
        # decision is retired from the same call that retired it — one round trip, and no window
        # where /agent/pending still offers work the agent has already done.
        resolved = _resolve_deferrals_for(deferrals, command, outcome)
        if resolved:
            outcome = {**outcome, "resolved_deferrals": resolved}
        return outcome

    @app.post("/agent/queue")
    def agent_queue_install(body: dict[str, object]) -> dict[str, object]:
        """Queue a standing answer, with the conditions under which it stops standing.

        `{"faction_id": 2, "surface_id": "base.production", "base_id": 7,
          "action_id": "facility:4", "until_turn": 60, "reason": "...",
          "predicates": [{"metric": "mineral_surplus", "comparator": "at_least", "target": 2}]}`

        A predicate names a metric from the measured vocabulary, a comparator and a number, and
        it reads as the condition under which the answer REMAINS valid. At least one is required
        and an unmeasurable one is REFUSED here — 422, while the agent is still holding the
        decision and can be told why. That refusal is the feature: an answer whose invalidation
        condition can never fire is a script, and a script keeps building Recycling Tanks through
        a drone riot.
        """
        for required in ("faction_id", "surface_id", "action_id"):
            if body.get(required) in (None, ""):
                raise HTTPException(422, f"{required} is required")
        faction_id = _as_int(body["faction_id"], "faction_id")
        surface_id = str(body["surface_id"])
        action_id = str(body["action_id"])

        raw_predicates = body.get("predicates") or []
        if not isinstance(raw_predicates, list):
            raise HTTPException(422, "predicates must be a list")
        predicates = []
        for raw in raw_predicates:
            if not isinstance(raw, dict):
                raise HTTPException(422, "each predicate must be an object")
            target = raw.get("target")
            if raw.get("metric") in (None, "") or not isinstance(target, int | float | str):
                raise HTTPException(422, "a predicate needs a metric and a numeric target")
            try:
                predicates.append(
                    Predicate(
                        metric=str(raw["metric"]),
                        comparator=cast(Comparator, str(raw.get("comparator", "at_least"))),
                        target=float(target),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, f"a predicate needs metric and target: {exc}") from exc

        answer = QueuedAnswer(
            faction_id=faction_id,
            surface_id=surface_id,
            action_id=action_id,
            predicates=predicates,
            base_id=_as_opt_int(body.get("base_id"), "base_id"),
            until_turn=_as_opt_int(body.get("until_turn"), "until_turn"),
            reason=str(body["reason"]) if body.get("reason") else None,
        )
        try:
            answer_queue.install(answer)
        except QueueError as exc:
            # 422 rather than 400: the request is well-formed and the CONTENT is unusable, which
            # is a distinction an agent can act on.
            raise HTTPException(422, str(exc)) from exc
        return {"queued": answer.summary()}

    @app.get("/agent/queue")
    def agent_queue_list(faction_id: int | None = None) -> dict[str, object]:
        """Standing answers, and the ones the board has already overtaken.

        `retired` is the more interesting half: each entry is a moment when something the agent
        said in advance would matter actually happened. A queue that never retires anything is
        either a very quiet game or a set of predicates that cannot fire.
        """
        standing = answer_queue.standing(faction_id)
        return {
            "standing": [a.summary() for a in standing],
            "count": len(standing),
            "retired": list(answer_queue.retired),
        }

    @app.post("/agent/plan")
    def agent_plan_install(body: dict[str, object]) -> dict[str, object]:
        """Install a faction's decision table for ONE turn — bulk-turn mode (na-7bk).

        `{"faction_id": 2, "turn": 43, "entries": [
           {"surface_id": "base.production", "base_id": 7, "action_id": "facility:4",
            "reason": "finish the Tanks"}, ...]}`

        The natural companion to `POST /agent/turn`: read the forecast, decide the whole turn at
        your own pace, install the table, and the orchestrator answers covered decisions in
        milliseconds at `tier="plan"` — waking you only for the ones the table does not cover.

        The table replaces the faction's previous one whole, and answers nothing outside the
        stated turn: by turn N+1 the engine has played turn N, so those answers were made
        against a board that no longer exists. An empty `entries` list is a legitimate install
        meaning "wake me for everything".
        """
        for required in ("faction_id", "turn"):
            if body.get(required) in (None, ""):
                raise HTTPException(
                    422,
                    f"{required} is required — a plan is faction-private and lives exactly one "
                    "turn, so both are part of its identity",
                )
        faction_id = _as_int(body["faction_id"], "faction_id")
        turn = _as_int(body["turn"], "turn")

        raw_entries = body.get("entries")
        if raw_entries is None:
            raw_entries = []
        if not isinstance(raw_entries, list):
            raise HTTPException(422, "entries must be a list")
        entries: dict[tuple[str, int | None], PlanEntry] = {}
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise HTTPException(422, "each plan entry must be an object")
            entry = PlanEntry(
                surface_id=str(raw.get("surface_id") or ""),
                action_id=str(raw.get("action_id") or ""),
                base_id=_as_opt_int(raw.get("base_id"), "entries[].base_id"),
                reason=str(raw["reason"]) if raw.get("reason") else None,
            )
            entries[(entry.surface_id, entry.base_id)] = entry

        installed = TurnPlan(faction_id=faction_id, turn=turn, entries=entries)
        try:
            validate_plan(installed)
        except PlanError as exc:
            # 422 while the agent still holds the strategy and can fix the entry it got wrong.
            raise HTTPException(422, str(exc)) from exc
        turn_plan.install(installed)
        return {"plan": installed.summary()}

    @app.get("/agent/plan")
    def agent_plan_list(faction_id: int | None = None) -> dict[str, object]:
        """The installed tables, what each entry has answered, and what missed.

        `missed` is the health signal: each entry there named an action that had left the space
        by the time its decision arrived, so the plan was written against a board that changed
        before the engine asked. A table that misses often is strategy set too early.
        """
        plans = turn_plan.plans(faction_id)
        return {
            "plans": [p.summary() for p in plans],
            "count": len(plans),
            "missed": list(turn_plan.missed),
        }

    @app.get("/agent/pending")
    def agent_pending(full: bool = False, faction_id: int | None = None) -> dict[str, object]:
        """Decisions the agent parked, and has not come back to yet (na-7bk).

        Mounted whatever the brain is, unlike the `/agent/*` endpoints above. Those are gated on
        an AgentBrain because they advertise a queue only an attached agent fills; a deferral is
        filled by whichever brain returned `defer`, and an empty list here is an honest answer
        rather than a misleading advertisement.

        `full=true` includes each deferral's world view — the grounded one the agent read when it
        deferred. That is the point of keeping it: coming back to a decision should not mean
        re-deriving the situation, and a resolution made from a different set of facts than the
        deferral was made on is a differently-uninformed answer, not a better one.

        Resolve through door 2 — `POST /order {"verb": "build", "args": [base_id, item_id]}`. A
        confirmed build closes the matching deferral and names it in that response.

        `faction_id` scopes the list, and an agent should always pass it: a deferral names a
        base and carries the grounded world view its faction read. Unscoped is the operator's
        read of the whole run, which is the same split `/turn` and `/agent/turn` make — the
        difference here is that this route is mounted whatever the brain is, so the scope is a
        parameter rather than a separate route.
        """
        items = deferrals.pending(faction_id)
        return {
            "pending": [
                ({**d.summary(), "world_view": d.world_view} if full else d.summary())
                for d in items
            ],
            "count": len(items),
        }

    @app.get("/order")
    def order_status() -> dict[str, object]:
        """Can orders be issued at all, and if not, why not.

        Worth its own route: an agent that finds `/order` returning `unavailable` on every call
        should be able to learn *why* without issuing an order to find out.
        """
        return {
            "available": order_channel.available,
            "reason": order_channel.why_unavailable() or None,
            "verbs": {
                "move": "move <veh_id> <x> <y>",
                "skip": "skip <veh_id>",
                "build": "build <base_id> <item_id>",
            },
        }

    @app.get("/coverage")
    def coverage() -> dict[str, object]:
        """Live coverage for this run — the assertion surface for the harness."""
        if orchestrator.log is None:
            return {"error": "no decision log configured (set NA_DECISION_LOG)"}
        return report(orchestrator.log.read()).summary()

    return app


app = create_app()
