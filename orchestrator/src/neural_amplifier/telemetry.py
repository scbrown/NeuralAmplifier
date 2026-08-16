"""Fan-out for the decision record — ``docs/observability.md`` §3.

Two layers: JSONL is the record of truth, OpenTelemetry is the live view. The
rule that makes them trustworthy is that the record is **assembled once** and
both layers are projections of that one object. If they can be built
independently, they will eventually disagree, and the JSONL will be the one
that's right while the dashboard is the one people look at.

So :class:`Emitter` takes an already-built :class:`~.decisions.DecisionRecord`
and hands *the same instance* to every sink. It also never raises: a telemetry
backend that is down must not stall the game (invariant #9), which is the same
reason a brain failure degrades rather than propagates. Failures are counted,
not swallowed — a silent exporter is the observability equivalent of the
all-fallback run.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from . import fairness
from .decisions import DecisionRecord

#: OTel semantic conventions put LLM attributes under ``gen_ai``; everything
#: game-specific is ours and namespaced to avoid colliding with future ones.
NAMESPACE = "na"


@runtime_checkable
class Sink(Protocol):
    """Anything that can receive a decision record.

    :class:`~.decisions.DecisionLog` already satisfies this — the JSONL writer
    is not a special case, it is just the first sink.
    """

    def write(self, record: DecisionRecord) -> None: ...


@dataclass
class SinkFailure:
    """A sink that raised. Kept rather than logged-and-forgotten so a run can
    assert its telemetry actually worked."""

    sink: str
    error: str


class Emitter:
    """The single emit call.

    Sinks are written **in order**, so put the record of truth first: if the
    OTel exporter blows up, the JSONL line is already on disk.
    """

    def __init__(self, *sinks: Sink) -> None:
        for sink in sinks:
            # emit() swallows per-call errors so telemetry can never stall the
            # game — which means a mis-wired sink would fail silently for a
            # whole run. Catch the shape mistake here instead, once, loudly.
            if not isinstance(sink, Sink):
                raise TypeError(
                    f"{type(sink).__name__} is not a Sink: it has no write(record). "
                    "A WorldViewStore belongs in Orchestrator(store=...), not sinks=[...]"
                )
        self.sinks: tuple[Sink, ...] = tuple(sinks)
        self.failures: list[SinkFailure] = []

    def emit(self, record: DecisionRecord) -> None:
        for sink in self.sinks:
            try:
                sink.write(record)
            except Exception as exc:  # noqa: BLE001 — telemetry must never stall the game
                self.failures.append(
                    SinkFailure(sink=type(sink).__name__, error=f"{type(exc).__name__}: {exc}")
                )

    @property
    def healthy(self) -> bool:
        return not self.failures


# --- W3C trace context ------------------------------------------------------


@dataclass(frozen=True)
class TraceContext:
    """A parsed ``traceparent``.

    The adapter is the **root** of the trace, because the game is the root of
    the causality (``docs/observability.md`` §4). The orchestrator continues
    this context rather than starting its own — otherwise a slow turn cannot be
    attributed across the orchestrator, Quipu, and Hank, which is the entire
    reason layer 2 exists.
    """

    trace_id: int
    span_id: int
    sampled: bool


def parse_traceparent(value: str | None) -> TraceContext | None:
    """Parse a W3C ``traceparent``, or ``None`` if it isn't one.

    Deliberately strict and dependency-free. A malformed header means we start
    a fresh trace, which loses correlation — bad, but better than attaching
    spans to a trace id we invented from a typo.
    """
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, span_id, flags = parts
    if len(version) != 2 or len(trace_id) != 32 or len(span_id) != 16 or len(flags) != 2:
        return None
    if version == "ff":  # forbidden by the spec
        return None
    try:
        ids = (int(trace_id, 16), int(span_id, 16), int(flags, 16))
    except ValueError:
        return None
    if ids[0] == 0 or ids[1] == 0:  # all-zero ids are invalid
        return None
    return TraceContext(trace_id=ids[0], span_id=ids[1], sampled=bool(ids[2] & 0x01))


# --- layer 2 ----------------------------------------------------------------


def attributes(record: DecisionRecord) -> dict[str, Any]:
    """Project a record onto span/metric attributes.

    A projection, not a translation — the JSONL field names were chosen to be
    OTel-shaped precisely so this stays mechanical and reviewable.
    """
    handicaps = list(record.fairness_profile)
    out: dict[str, Any] = {
        f"{NAMESPACE}.game_id": record.game_id or "",
        f"{NAMESPACE}.turn": record.turn,
        f"{NAMESPACE}.faction": record.faction,
        f"{NAMESPACE}.engine": record.engine,
        f"{NAMESPACE}.surface_id": record.surface_id or "",
        f"{NAMESPACE}.scope": record.scope,
        f"{NAMESPACE}.tier": record.tier,
        f"{NAMESPACE}.world_view_hash": record.world_view_hash,
        f"{NAMESPACE}.action_space_size": record.action_space_size,
        f"{NAMESPACE}.degraded": record.degraded,
        f"{NAMESPACE}.adherence_violations": record.adherence_violations,
        f"{NAMESPACE}.repeated_actions": record.repeated_actions,
        # Carried on every span so a result stays interpretable in the live
        # view too, not only in the JSONL after the fact.
        f"{NAMESPACE}.fairness.handicaps": handicaps,
        f"{NAMESPACE}.fairness.structural": sorted(
            h for h in handicaps if fairness.is_structural(h)
        ),
    }
    block = record.knowledge
    if block.quipu_hits or block.hank_verdict or block.quipu_degraded or block.hank_degraded:
        out[f"{NAMESPACE}.quipu.hits"] = block.quipu_hits
        out[f"{NAMESPACE}.hank.verdict"] = block.hank_verdict or ""
        out[f"{NAMESPACE}.hank.stripped"] = list(block.stripped)
    if record.year is not None:
        out[f"{NAMESPACE}.year"] = record.year
    if record.degrade_reason:
        out[f"{NAMESPACE}.degrade_reason"] = record.degrade_reason
    if record.model:
        out["gen_ai.request.model"] = record.model
        out["gen_ai.usage.input_tokens"] = record.tokens.input
        out["gen_ai.usage.output_tokens"] = record.tokens.output
        out["gen_ai.usage.cached_tokens"] = record.tokens.cached
    return out


#: Attributes safe to put on a metric. Everything else on a span — `game_id`,
#: `world_view_hash`, `turn` — is unique per decision and would turn each one
#: into its own time series.
METRIC_KEYS = ("surface_id", "scope", "tier", "engine", "degraded")


def metric_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """The bounded-cardinality subset of :func:`attributes`."""
    return {key: attrs[f"{NAMESPACE}.{key}"] for key in METRIC_KEYS}


class OtelSink:
    """Emits one span and the §6 ops metrics per decision.

    The span is created with explicit start/end times derived from
    ``latency_ms``, because by the time a record exists the decision is already
    over — a span opened now would measure the exporter, not the model.
    """

    def __init__(
        self,
        tracer: Any | None = None,
        meter: Any | None = None,
        service_name: str = "neural-amplifier",
    ) -> None:
        try:
            from opentelemetry import metrics, trace
        except ImportError as exc:  # pragma: no cover - exercised by env, not tests
            raise ImportError(
                "OtelSink needs the optional OpenTelemetry dependency: "
                "`uv sync --extra otel`. Layer 1 (JSONL) works without it."
            ) from exc

        self._trace = trace
        self.tracer = tracer or trace.get_tracer(service_name)
        meter = meter or metrics.get_meter(service_name)

        # RED plus the domain signals from docs/observability.md §6. Named to
        # match that table so a dashboard maps onto the doc one-for-one.
        self.latency = meter.create_histogram(
            f"{NAMESPACE}.decision.latency", unit="ms", description="Time to produce a decision"
        )
        self.decisions = meter.create_counter(
            f"{NAMESPACE}.decision.count",
            description="Decisions, split by tier and degradation",
        )
        self.action_space = meter.create_histogram(
            f"{NAMESPACE}.action_space.size", description="Legal actions offered per decision"
        )
        self.tokens = meter.create_counter(
            f"{NAMESPACE}.tokens", description="Token usage, split by kind"
        )

    def write(self, record: DecisionRecord) -> None:
        attrs = attributes(record)
        self._span(record, attrs)
        self._metrics(record, attrs)

    def _knowledge_spans(self, record: DecisionRecord, parent: Any, end: int) -> None:
        """Child spans for the knowledge layer (``docs/observability.md`` §4).

        Without these, a slow turn is attributable only to "the decision" —
        the whole point of layer 2 is telling a Quipu retrieval apart from the
        model. Emitted only for layers that actually ran.
        """
        trace = self._trace
        block = record.knowledge
        context = trace.set_span_in_context(parent)
        legs = (
            ("quipu.retrieve", block.quipu_latency_ms, block.quipu_degraded, block.quipu_hits > 0),
            (
                "hank.policy_guard",
                block.hank_latency_ms,
                block.hank_degraded,
                block.hank_verdict is not None,
            ),
        )
        for name, latency_ms, degraded, ran in legs:
            if not ran and not degraded:
                continue
            child_attrs: dict[str, Any] = {f"{NAMESPACE}.degraded": degraded}
            if name.startswith("quipu"):
                child_attrs[f"{NAMESPACE}.quipu.hits"] = block.quipu_hits
            else:
                child_attrs[f"{NAMESPACE}.hank.verdict"] = block.hank_verdict or ""
                child_attrs[f"{NAMESPACE}.hank.stripped"] = list(block.stripped)
            child = self.tracer.start_span(
                name,
                context=context,
                start_time=end - max(latency_ms, 0) * 1_000_000,
                attributes=child_attrs,
            )
            if degraded:
                child.set_status(trace.Status(trace.StatusCode.ERROR, f"{name} unavailable"))
            child.end(end_time=end)

    def _span(self, record: DecisionRecord, attrs: dict[str, Any]) -> None:
        trace = self._trace
        parent = parse_traceparent(record.trace_id)
        context = None
        if parent is not None:
            context = trace.set_span_in_context(
                trace.NonRecordingSpan(
                    trace.SpanContext(
                        trace_id=parent.trace_id,
                        span_id=parent.span_id,
                        is_remote=True,
                        trace_flags=trace.TraceFlags(
                            trace.TraceFlags.SAMPLED if parent.sampled else trace.TraceFlags.DEFAULT
                        ),
                    )
                )
            )

        end = time.time_ns()
        start = end - max(record.latency_ms or 0, 0) * 1_000_000
        # Low cardinality by construction: surface ids come from a frozen
        # registry of 77 (docs/game-surface.md §1).
        name = f"decision {record.surface_id or 'unknown'}"
        span = self.tracer.start_span(name, context=context, start_time=start, attributes=attrs)
        if record.degraded:
            # Surfacing degradation as span status means any tracing UI shows
            # an all-fallback run as red without anyone writing a query first.
            span.set_status(
                trace.Status(trace.StatusCode.ERROR, record.degrade_reason or "degraded")
            )
        self._knowledge_spans(record, span, end)
        span.end(end_time=end)

    def _metrics(self, record: DecisionRecord, attrs: dict[str, Any]) -> None:
        common = metric_attributes(attrs)

        if record.latency_ms is not None:
            self.latency.record(record.latency_ms, common)
        self.decisions.add(1, common)
        self.action_space.record(record.action_space_size, common)

        usage = {"input": record.tokens.input, "output": record.tokens.output}
        # cache.hit_rate is cached/input, so both have to be counted separately.
        usage["cached"] = record.tokens.cached
        for kind, count in usage.items():
            if count:
                self.tokens.add(count, {**common, "kind": kind})


def sinks_for(
    log: Sink | None = None,
    extra: Sequence[Sink] = (),
    otel: bool = False,
) -> tuple[Sink, ...]:
    """Assemble a sink list, record of truth first.

    ``otel=True`` raises if the dependency is absent rather than degrading
    quietly — asking for the live view and not getting it should be loud.
    """
    out: list[Sink] = [log] if log is not None else []
    if otel:
        out.append(OtelSink())
    out.extend(extra)
    return tuple(out)
