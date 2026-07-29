"""Layer 2 — the OTel projection, and the invariant that keeps it honest.

The claim being tested is ``docs/observability.md`` §3: both layers are fed
from one emit call and can therefore never disagree. That is an identity
assertion, not a "the fields look similar" assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from neural_amplifier.brain import BrainError, ScriptedBrain
from neural_amplifier.contract import WorldView
from neural_amplifier.decisions import DecisionLog, DecisionRecord
from neural_amplifier.fairness import fairness_profile
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.telemetry import (
    Emitter,
    OtelSink,
    attributes,
    metric_attributes,
    parse_traceparent,
)

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class Recorder:
    """A sink that keeps what it was handed, by identity."""

    def __init__(self) -> None:
        self.seen: list[DecisionRecord] = []

    def write(self, record: DecisionRecord) -> None:
        self.seen.append(record)


class Broken:
    def __init__(self) -> None:
        self.calls = 0

    def write(self, record: DecisionRecord) -> None:
        self.calls += 1
        raise RuntimeError("collector unreachable")


Harness = tuple[OtelSink, InMemorySpanExporter, InMemoryMetricReader]


@pytest.fixture
def otel() -> Harness:
    """In-memory tracer and meter — the exporter is testable with no collector,
    which is the point of keeping layer 2 a projection."""
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    sink = OtelSink(
        tracer=tracer_provider.get_tracer("test"),
        meter=MeterProvider(metric_readers=[reader]).get_meter("test"),
    )
    return sink, exporter, reader


# --- the invariant ---------------------------------------------------------


def test_every_sink_receives_the_same_object(thinker_base: WorldView, tmp_path: Path) -> None:
    """Not "equal fields" — the *same instance*. Assembling the record twice is
    how the dashboard and the log drift apart, so make it structurally
    impossible rather than a convention."""
    first, second = Recorder(), Recorder()
    result = Orchestrator(ScriptedBrain(), sinks=[first, second]).decide(thinker_base)

    assert first.seen[0] is result.record
    assert second.seen[0] is result.record


def test_a_failing_exporter_does_not_stall_the_game(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """Same invariant as a brain failure: telemetry egress must never propagate
    into the decision loop."""
    log = DecisionLog(tmp_path / "d.jsonl")
    orchestrator = Orchestrator(ScriptedBrain(), log=log, sinks=[Broken()])

    result = orchestrator.decide(thinker_base)

    assert result.orders.choices  # the turn still happened
    assert len(list(log.read())) == 1  # ...and the record of truth still landed


def test_exporter_failures_are_counted_not_swallowed(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """A silent exporter is the observability twin of the all-fallback run."""
    orchestrator = Orchestrator(ScriptedBrain(), sinks=[Broken()])
    orchestrator.decide(thinker_base)

    assert orchestrator.telemetry.healthy is False
    assert orchestrator.telemetry.failures[0].sink == "Broken"
    assert "collector unreachable" in orchestrator.telemetry.failures[0].error


def test_the_record_of_truth_is_written_first(thinker_base: WorldView, tmp_path: Path) -> None:
    """Ordering is the reason a downstream failure is survivable."""
    log = DecisionLog(tmp_path / "d.jsonl")
    orchestrator = Orchestrator(ScriptedBrain(), log=log, sinks=[Recorder()])
    assert orchestrator.telemetry.sinks[0] is log


def test_an_emitter_with_no_sinks_is_a_no_op() -> None:
    """Local runs export to nothing and lose only the live view."""
    emitter = Emitter()
    emitter.emit(
        DecisionRecord(
            turn=1,
            faction="GAIANS",
            engine="thinker",
            scope="turn",
            tier="llm",
            world_view_hash="sha256:x",
            action_space_size=0,
        )
    )
    assert emitter.healthy


# --- W3C trace context -----------------------------------------------------


def test_traceparent_round_trips() -> None:
    parsed = parse_traceparent(TRACEPARENT)
    assert parsed is not None
    assert f"{parsed.trace_id:032x}" == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert f"{parsed.span_id:016x}" == "00f067aa0ba902b7"
    assert parsed.sampled is True


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "not-a-traceparent",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",  # too few parts
        "00-4bf92f35-00f067aa0ba902b7-01",  # short trace id
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-0",  # short flags
        "00-000000000000000000000000000000zz-00f067aa0ba902b7-01",  # not hex
        "00-00000000000000000000000000000000-00f067aa0ba902b7-01",  # zero trace id
        "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",  # zero span id
        "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # forbidden version
    ],
)
def test_a_malformed_traceparent_is_rejected(value: str | None) -> None:
    """Better to start a fresh trace than to attach spans to an id we invented
    out of a typo."""
    assert parse_traceparent(value) is None


def test_the_adapter_is_the_trace_root(
    otel: Harness,
    thinker_base: WorldView,
) -> None:
    """The game is the root of the causality, so the orchestrator *continues*
    the adapter's trace. Without this a slow turn cannot be attributed across
    the orchestrator, Quipu, and Hank — the whole reason layer 2 exists."""
    sink, spans, _ = otel
    assert thinker_base.traceparent() == TRACEPARENT  # the fixture is the adapter here
    Orchestrator(ScriptedBrain(), sinks=[sink]).decide(thinker_base)

    span = spans.get_finished_spans()[0]
    assert f"{span.context.trace_id:032x}" == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert span.parent is not None
    assert f"{span.parent.span_id:016x}" == "00f067aa0ba902b7"


def test_a_world_view_without_a_traceparent_still_produces_a_span(
    otel: Harness,
    thinker_base: WorldView,
) -> None:
    """An adapter that omits the field still works; it just loses correlation."""
    sink, spans, _ = otel
    Orchestrator(ScriptedBrain(), sinks=[sink]).decide(
        thinker_base.model_copy(update={"trace": None})
    )

    span = spans.get_finished_spans()[0]
    assert span.parent is None
    assert span.context.trace_id != 0


# --- the projection --------------------------------------------------------


def test_span_carries_the_decision_identity(
    otel: Harness,
    thinker_base: WorldView,
) -> None:
    sink, spans, _ = otel
    view = thinker_base.model_copy(update={"fairness": fairness_profile("ai", "transcend")})
    result = Orchestrator(ScriptedBrain(), sinks=[sink]).decide(view)

    span = spans.get_finished_spans()[0]
    assert span.name == "decision base.production"
    assert span.attributes["na.surface_id"] == "base.production"
    assert span.attributes["na.world_view_hash"] == result.record.world_view_hash
    # The fairness profile rides along, so the live view is interpretable too.
    assert "retool_penalty" in span.attributes["na.fairness.structural"]
    assert "tech_cost_factor" not in span.attributes["na.fairness.structural"]


def test_span_name_stays_low_cardinality(
    otel: Harness,
    thinker_base: WorldView,
) -> None:
    """Surface ids come from a frozen registry; game_id and turn must not leak
    into the name or every span becomes its own series."""
    sink, spans, _ = otel
    orchestrator = Orchestrator(ScriptedBrain(), sinks=[sink], game_id="game-abc")
    orchestrator.decide(thinker_base)
    orchestrator.decide(thinker_base.model_copy(update={"turn": 99}))

    names = {s.name for s in spans.get_finished_spans()}
    assert names == {"decision base.production"}


def test_degradation_shows_as_a_failed_span(
    otel: Harness,
    thinker_base: WorldView,
) -> None:
    """An all-fallback run should read as red in any tracing UI without anyone
    having to write a query first (§5.4)."""
    sink, spans, _ = otel
    Orchestrator(ScriptedBrain(raises=BrainError("down")), sinks=[sink]).decide(thinker_base)

    span = spans.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["na.degraded"] is True
    assert "down" in (span.status.description or "")


def test_span_measures_the_decision_not_the_exporter(otel: Harness) -> None:
    """The decision is already over when the record exists, so the span is
    reconstructed from latency_ms rather than timed live."""
    sink, spans, _ = otel
    sink.write(
        DecisionRecord(
            turn=1,
            faction="GAIANS",
            engine="thinker",
            scope="turn",
            tier="llm",
            world_view_hash="sha256:x",
            action_space_size=3,
            latency_ms=1500,
        )
    )
    span = spans.get_finished_spans()[0]
    assert (span.end_time - span.start_time) == 1_500_000_000


def test_ops_signals_are_recorded(
    otel: Harness,
    thinker_base: WorldView,
) -> None:
    """The §6 table, by name — a dashboard should map onto the doc one-for-one."""
    sink, _, reader = otel
    Orchestrator(ScriptedBrain(), sinks=[sink]).decide(thinker_base)

    data = reader.get_metrics_data()
    emitted = {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {"na.decision.latency", "na.decision.count", "na.action_space.size"} <= emitted


def test_tokens_are_split_by_kind(otel: Harness) -> None:
    """cache.hit_rate is cached/input, so they cannot share a series."""
    sink, _, reader = otel
    sink.write(
        DecisionRecord(
            turn=1,
            faction="GAIANS",
            engine="thinker",
            scope="turn",
            tier="llm",
            world_view_hash="sha256:x",
            action_space_size=3,
            model="claude-opus-5",
            tokens={"input": 900, "output": 40, "cached": 800},  # type: ignore[arg-type]
        )
    )

    points = [
        (point.attributes["kind"], point.value)
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "na.tokens"
        for point in metric.data.data_points
    ]
    assert dict(points) == {"input": 900, "output": 40, "cached": 800}


def test_metrics_drop_the_unbounded_attributes() -> None:
    """Spans can carry per-decision identity; metrics cannot. game_id, turn,
    and world_view_hash would make every decision its own time series, and
    histograms can't take the fairness list at all."""
    record = DecisionRecord(
        turn=1,
        faction="GAIANS",
        engine="thinker",
        scope="turn",
        tier="llm",
        world_view_hash="sha256:x",
        action_space_size=3,
        game_id="game-abc",
        fairness_profile=["retool_penalty"],
    )
    attrs = attributes(record)
    assert attrs["na.game_id"] == "game-abc"
    assert attrs["na.fairness.handicaps"] == ["retool_penalty"]

    metric = metric_attributes(attrs)
    assert set(metric) == {"surface_id", "scope", "tier", "engine", "degraded"}
    assert all(isinstance(v, str | bool) for v in metric.values())
