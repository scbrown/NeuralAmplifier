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
from .telemetry import OtelSink, Sink


def build_brain() -> Brain:
    """Scripted by default; the real brain is opt-in.

    Tests and CI must never make a paid API call by accident, so the real brain
    requires ``NA_BRAIN=claude`` explicitly.
    """
    if os.environ.get("NA_BRAIN", "scripted").lower() == "claude":
        return ClaudeBrain()
    return ScriptedBrain()


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

    orchestrator = Orchestrator(
        brain=brain or build_brain(), log=resolved_log, sinks=resolved_sinks
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
