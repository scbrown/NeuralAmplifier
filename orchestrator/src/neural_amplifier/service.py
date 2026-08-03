"""The HTTP surface: ``POST /decide``.

One request per decision point, exactly as ``docs/contract.md`` describes.
Keeping it plain JSON over HTTP is what makes every decision inspectable,
loggable, and replayable — and it is what step 7 of the observability plan
(replay as regression) spends.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from .agent_brain import AgentBrain
from .brain import Brain, ClaudeBrain, ScriptedBrain
from .config import Config
from .config import load as load_config
from .contract import Choice, Directive, Orders, WorldView
from .coverage import report
from .decisions import DecisionLog
from .directives import DirectiveStore, accept
from .orchestrator import Orchestrator
from .outcomes import EngineOutcome, OutcomeStore
from .pending import NotClaimable, Pending
from .replay import WorldViewStore
from .telemetry import OtelSink, Sink


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


def _build_retriever(config: Config) -> object | None:
    """Quipu-backed grounding, opt-in via ``knowledge.quipu_url``.

    Absent means an ungrounded decision, never a failed start — the
    knowledge layer is an optimisation (``knowledge.py``).
    """
    if not config.knowledge.quipu_url:
        return None
    from .datalinks import QuipuRetriever

    return QuipuRetriever(
        config.knowledge.quipu_url,
        engine=config.knowledge.engine,
        token_budget=config.knowledge.token_budget,
    )


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

    Set ``knowledge.guard = false`` (or NA_HANK_GUARD=0) to disable. When Hank's own
    POST /guard lands it replaces this, and the verdict shape is already what that surface
    returns.
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
    return GuardChain(*guards)


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
    orchestrator = Orchestrator(
        brain=brain or build_brain(config),
        log=resolved_log,
        sinks=resolved_sinks,
        store=WorldViewStore(store_path) if store_path else None,
        retriever=resolved_retriever,  # type: ignore[arg-type]
        guard=build_guard(resolved_retriever, config),  # type: ignore[arg-type]
        plan=DirectiveStore(Path(plan_path)) if plan_path else None,
        policy=config.surfaces,
    )
    app.state.orchestrator = orchestrator

    # Engine-side outcomes (outcomes.py). Distinct from the brain's `publish_outcome`, which
    # reports what the ORCHESTRATOR applied; this is what the ENGINE did with it afterwards.
    outcome_store = OutcomeStore()
    app.state.outcomes = outcome_store

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
        result = orchestrator.decide(world_view)
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
            # A JSON body is `object`-valued, so `wait` is whatever the caller sent. A
            # non-numeric one means "do not block" rather than a 500: this endpoint is the
            # agent's only way to collect work, and failing it over a malformed optional
            # costs more than ignoring the field.
            raw_wait = (body or {}).get("wait", 0) or 0
            wait = float(raw_wait) if isinstance(raw_wait, int | float | str) else 0.0
            pending = queue.claim(wait=min(wait, 110.0))
            if pending is None:
                return {"decision_id": None, "waiting": 0}
            return {
                "decision_id": pending.id,
                "surface_id": pending.world_view.surface_id,
                "world_view": pending.world_view.model_dump(exclude_none=True),
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

    @app.get("/coverage")
    def coverage() -> dict[str, object]:
        """Live coverage for this run — the assertion surface for the harness."""
        if orchestrator.log is None:
            return {"error": "no decision log configured (set NA_DECISION_LOG)"}
        return report(orchestrator.log.read()).summary()

    return app


app = create_app()
