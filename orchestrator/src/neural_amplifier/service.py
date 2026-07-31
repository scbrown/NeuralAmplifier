"""The HTTP surface: ``POST /decide``.

One request per decision point, exactly as ``docs/contract.md`` describes.
Keeping it plain JSON over HTTP is what makes every decision inspectable,
loggable, and replayable — and it is what step 7 of the observability plan
(replay as regression) spends.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .agent_brain import AgentBrain
from .brain import Brain, ClaudeBrain, ScriptedBrain
from .contract import Choice, Orders, WorldView
from .coverage import report
from .decisions import DecisionLog
from .orchestrator import Orchestrator
from .pending import NotClaimable
from .replay import WorldViewStore
from .telemetry import OtelSink, Sink


def build_brain() -> Brain:
    """Scripted by default; every other brain is opt-in.

    Tests and CI must never make a paid API call by accident, so the real brain
    requires ``NA_BRAIN=claude`` explicitly.

    ``NA_BRAIN=agent`` hands decisions to an attached MCP client — Claude Code in a tmux pane,
    or anything else that speaks the tool surface. It makes no API call of its own, but it does
    *block* until something answers, so it is opt-in for a different reason: a service started
    with it by accident would look hung rather than idle.
    """
    kind = os.environ.get("NA_BRAIN", "scripted").lower()
    if kind == "claude":
        return ClaudeBrain()
    if kind == "agent":
        return AgentBrain()
    return ScriptedBrain()


def _build_retriever() -> object | None:
    """Quipu-backed grounding, opt-in via NA_QUIPU_URL.

    Absent means an ungrounded decision, never a failed start — the
    knowledge layer is an optimisation (``knowledge.py``).
    """
    url = os.environ.get("NA_QUIPU_URL")
    if not url:
        return None
    from .datalinks import QuipuRetriever

    return QuipuRetriever(url, engine=os.environ.get("NA_ENGINE", "thinker"))


def build_guard(retriever: object | None) -> object | None:
    """The citation-integrity guard, on whenever grounding is.

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

    Set NA_HANK_GUARD=0 to disable. When Hank's own POST /guard lands it replaces this, and
    the verdict shape is already what that surface returns.
    """
    if retriever is None:
        return None
    if os.environ.get("NA_HANK_GUARD", "1").lower() in {"0", "false", "no"}:
        return None
    from .hank import CitationGuard

    return CitationGuard()


def _otel_requested() -> bool:
    return os.environ.get("NA_OTEL", "").lower() in {"1", "true", "yes"}


def create_app(
    brain: Brain | None = None,
    log: DecisionLog | None = None,
    sinks: Sequence[Sink] | None = None,
) -> FastAPI:
    app = FastAPI(title="Neural Amplifier orchestrator", version="0.1.0")

    resolved_log = log
    if resolved_log is None:
        path = os.environ.get("NA_DECISION_LOG")
        if path:
            resolved_log = DecisionLog(Path(path))

    # Layer 2 is opt-in and loud: NA_OTEL=1 without the extra installed raises
    # at startup rather than serving a run with no live view (§3).
    resolved_sinks = (
        list(sinks) if sinks is not None else ([OtelSink()] if _otel_requested() else [])
    )

    # Without this a run's log references inputs nobody kept, and replay
    # (observability step 7) has nothing to feed back.
    store_path = os.environ.get("NA_WORLD_VIEW_STORE")
    resolved_retriever = _build_retriever()
    orchestrator = Orchestrator(
        brain=brain or build_brain(),
        log=resolved_log,
        sinks=resolved_sinks,
        store=WorldViewStore(store_path) if store_path else None,
        retriever=resolved_retriever,  # type: ignore[arg-type]
        guard=build_guard(resolved_retriever),  # type: ignore[arg-type]
    )
    app.state.orchestrator = orchestrator

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
        return orchestrator.decide(world_view).orders

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
            wait = float((body or {}).get("wait", 0) or 0)
            pending = queue.claim(wait=min(wait, 110.0))
            if pending is None:
                return {"decision_id": None, "waiting": 0}
            return {
                "decision_id": pending.id,
                "surface_id": pending.world_view.surface_id,
                "world_view": pending.world_view.model_dump(exclude_none=True),
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
            try:
                answered = queue.answer(
                    decision_id,
                    Orders(choices=[Choice(action_id=action_id, reason=str(reason or "") or None)]),
                )
            except NotClaimable as exc:
                raise HTTPException(409, str(exc)) from exc
            return {
                "decision_id": answered.id,
                "accepted": action_id,
                "status": "applied to the game",
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

    @app.get("/coverage")
    def coverage() -> dict[str, object]:
        """Live coverage for this run — the assertion surface for the harness."""
        if orchestrator.log is None:
            return {"error": "no decision log configured (set NA_DECISION_LOG)"}
        return report(orchestrator.log.read()).summary()

    return app


app = create_app()
