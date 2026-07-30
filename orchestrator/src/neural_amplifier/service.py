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

from fastapi import FastAPI

from .brain import Brain, ClaudeBrain, ScriptedBrain
from .contract import Orders, WorldView
from .coverage import report
from .decisions import DecisionLog
from .orchestrator import Orchestrator
from .replay import WorldViewStore
from .telemetry import OtelSink, Sink


def build_brain() -> Brain:
    """Scripted by default; the real brain is opt-in.

    Tests and CI must never make a paid API call by accident, so the real brain
    requires ``NA_BRAIN=claude`` explicitly.
    """
    if os.environ.get("NA_BRAIN", "scripted").lower() == "claude":
        return ClaudeBrain()
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

    @app.get("/coverage")
    def coverage() -> dict[str, object]:
        """Live coverage for this run — the assertion surface for the harness."""
        if orchestrator.log is None:
            return {"error": "no decision log configured (set NA_DECISION_LOG)"}
        return report(orchestrator.log.read()).summary()

    return app


app = create_app()
