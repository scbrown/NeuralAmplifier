"""The HTTP surface: ``POST /decide``.

One request per decision point, exactly as ``docs/contract.md`` describes.
Keeping it plain JSON over HTTP is what makes every decision inspectable,
loggable, and replayable — and it is what step 7 of the observability plan
(replay as regression) spends.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI

from .brain import Brain, ClaudeBrain, ScriptedBrain
from .config import Config
from .config import load as load_config
from .contract import Orders, WorldView
from .coverage import report
from .decisions import DecisionLog
from .orchestrator import Orchestrator
from .replay import WorldViewStore
from .telemetry import OtelSink, Sink


def build_brain(config: Config | None = None) -> Brain:
    """Scripted by default; the real brain is opt-in.

    Tests and CI must never make a paid API call by accident, so the real brain requires
    ``kind = "claude"`` in ``na.toml`` or ``NA_BRAIN=claude`` explicitly.
    """
    cfg = (config or load_config()).brain
    if cfg.kind != "claude":
        return ScriptedBrain()
    kwargs: dict[str, str] = {}
    if cfg.model:
        kwargs["model"] = cfg.model
    if cfg.effort:
        kwargs["effort"] = cfg.effort
    return ClaudeBrain(**kwargs)  # type: ignore[arg-type]


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

    Set ``knowledge.guard = false`` (or NA_HANK_GUARD=0) to disable. When Hank's own
    POST /guard lands it replaces this, and the verdict shape is already what that surface
    returns.
    """
    if retriever is None:
        return None
    if not (config or load_config()).knowledge.guard:
        return None
    from .hank import CitationGuard

    return CitationGuard()


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
    orchestrator = Orchestrator(
        brain=brain or build_brain(config),
        log=resolved_log,
        sinks=resolved_sinks,
        store=WorldViewStore(store_path) if store_path else None,
        retriever=resolved_retriever,  # type: ignore[arg-type]
        guard=build_guard(resolved_retriever, config),  # type: ignore[arg-type]
        policy=config.surfaces,
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
