"""The seam Quipu and Hank plug into — ``docs/knowledge-architecture.md``.

None of K1–K6 is built. What exists here is the *shape*: two protocols, their
result types, and the wiring that makes them optional. Building it now is cheap
and building it later is surgery, because the two things that are hard to
retrofit are exactly the two things this file fixes in place:

**Degradation.** The knowledge layer is an optimisation, not a dependency. A
Quipu that is down means a less-informed decision, never a stalled turn — the
same invariant #9 that governs the brain and the telemetry sinks. A retriever
or guard that raises is caught here and the decision proceeds without it, with
``degraded`` set so the run is not mistaken for a well-informed one.

**Provenance.** ``docs/observability.md`` §7 calls this the honesty half: the
record has to distinguish "the brain was told this" from "the brain assumed
this", and whether the policy guard modified the move. That only works if the
counts are captured at the point of use, so :class:`Knowledge` is assembled
during the decision and lands on the record.

**Precedence** is enforced by *order*, not by policy code
(``knowledge-architecture.md``): engine legality first — the guard never sees an
action the engine did not offer — then Hank deny, then everything softer. So
retrieval runs before the brain and the guard runs after action-space
validation, and nothing here can widen the action space.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from .contract import Orders, WorldView

Verdict = Literal["allow", "warn", "deny"]


@dataclass(frozen=True)
class Grounding:
    """What retrieval added to the prompt (Quipu, roles K1–K3)."""

    #: Ranked facts to put in front of the model. Deliberately opaque strings —
    #: the orchestrator never learns the graph, only that it was consulted.
    facts: tuple[str, ...] = ()
    #: Retrieval couldn't run. Not the same as "found nothing": one is a less
    #: informed decision, the other is a genuinely empty result.
    degraded: bool = False
    reason: str | None = None
    latency_ms: int = 0

    @property
    def hits(self) -> int:
        return len(self.facts)


@dataclass(frozen=True)
class Ruling:
    """What the policy guard said about the proposed orders (Hank, role c)."""

    verdict: Verdict = "allow"
    #: Action ids the guard removed. Deny strips; warn never does.
    stripped: tuple[str, ...] = ()
    #: Warn-level notes, surfaced to the model rather than enforced.
    advisories: tuple[str, ...] = ()
    degraded: bool = False
    reason: str | None = None
    latency_ms: int = 0


@dataclass
class Knowledge:
    """The provenance block that lands on the decision record."""

    quipu_hits: int = 0
    hank_verdict: Verdict | None = None
    stripped: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    quipu_degraded: bool = False
    hank_degraded: bool = False
    quipu_latency_ms: int = 0
    hank_latency_ms: int = 0

    @property
    def consulted(self) -> bool:
        """Whether either layer actually contributed.

        False on a run with no knowledge layer wired *and* on one where both
        were down — the record's ``*_degraded`` flags are what separate those.
        """
        return self.quipu_hits > 0 or self.hank_verdict is not None


@runtime_checkable
class Retriever(Protocol):
    """Annotates a decision with retrieved facts. Quipu implements this."""

    def retrieve(self, world_view: WorldView) -> Grounding: ...


@runtime_checkable
class Guard(Protocol):
    """Rules on already-legal orders. Hank implements this."""

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling: ...


def retrieve(retriever: Retriever | None, world_view: WorldView) -> Grounding:
    """Run retrieval, absorbing any failure into a degraded result."""
    if retriever is None:
        return Grounding()
    started = time.monotonic()
    try:
        result = retriever.retrieve(world_view)
    except Exception as exc:  # noqa: BLE001 — knowledge is an optimisation
        return Grounding(
            degraded=True,
            reason=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed(started),
        )
    return result if result.latency_ms else _with_latency(result, _elapsed(started))


def rule(guard: Guard | None, orders: Orders, world_view: WorldView) -> Ruling:
    """Run the policy guard, absorbing any failure into a degraded allow.

    A guard that is down **allows**. That is the deliberate choice: the engine's
    own legality check still stands behind this, and a guard failure that
    silently blocked every move would stall the game to enforce a policy nobody
    could read.
    """
    if guard is None:
        return Ruling(verdict="allow")
    started = time.monotonic()
    try:
        result = guard.rule(orders, world_view)
    except Exception as exc:  # noqa: BLE001
        return Ruling(
            verdict="allow",
            degraded=True,
            reason=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed(started),
        )
    return result if result.latency_ms else _with_ruling_latency(result, _elapsed(started))


def apply(orders: Orders, ruling: Ruling) -> Orders:
    """Strip denied choices. Warn-level advisories never remove anything."""
    if ruling.verdict != "deny" or not ruling.stripped:
        return orders
    denied = set(ruling.stripped)
    kept = [c for c in orders.choices if c.action_id not in denied]
    return orders.model_copy(update={"choices": kept})


def summarise(grounding: Grounding, ruling: Ruling, guarded: bool) -> Knowledge:
    """Fold both results into the record's provenance block."""
    return Knowledge(
        quipu_hits=grounding.hits,
        hank_verdict=ruling.verdict if guarded else None,
        stripped=list(ruling.stripped),
        advisories=list(ruling.advisories),
        quipu_degraded=grounding.degraded,
        hank_degraded=ruling.degraded,
        quipu_latency_ms=grounding.latency_ms,
        hank_latency_ms=ruling.latency_ms,
    )


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _with_latency(grounding: Grounding, latency_ms: int) -> Grounding:
    return Grounding(
        facts=grounding.facts,
        degraded=grounding.degraded,
        reason=grounding.reason,
        latency_ms=latency_ms,
    )


def _with_ruling_latency(ruling: Ruling, latency_ms: int) -> Ruling:
    return Ruling(
        verdict=ruling.verdict,
        stripped=ruling.stripped,
        advisories=ruling.advisories,
        degraded=ruling.degraded,
        reason=ruling.reason,
        latency_ms=latency_ms,
    )
