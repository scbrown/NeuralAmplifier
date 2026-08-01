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
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, runtime_checkable

from .contract import Orders, WorldView

Verdict = Literal["allow", "warn", "deny"]


@dataclass(frozen=True)
class Grounding:
    """What retrieval added to the prompt (Quipu, roles K1–K3)."""

    #: Ranked facts to put in front of the model. Deliberately opaque strings —
    #: the orchestrator never learns the graph, only that it was consulted.
    facts: tuple[str, ...] = ()
    #: Stable, short id per fact, positionally aligned with ``facts``. These are what
    #: the brain cites and what lands on the record, so a run can be audited without
    #: storing the fact text twice. Empty means the retriever does not label its
    #: facts, and utilisation is then unmeasurable rather than zero.
    fact_ids: tuple[str, ...] = ()
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
    #: Ids of the facts put in front of the model.
    quipu_facts: list[str] = field(default_factory=list)
    #: Ids the model said it relied on. The gap between this and ``quipu_facts`` is the
    #: only evidence that retrieval *mattered*: a run where twelve facts were retrieved
    #: and all ignored is otherwise indistinguishable from one where they drove the
    #: decision, because ``quipu_hits`` counts what was offered, not what was used.
    quipu_cited: list[str] = field(default_factory=list)
    #: True when no retriever was configured at all. Distinct from ``quipu_degraded``
    #: (one was configured and failed) and from ``hits == 0`` (it ran and found
    #: nothing). Collapsing the three is how a knowledge layer silently stops being
    #: wired and nobody notices for weeks.
    quipu_absent: bool = False
    hank_verdict: Verdict | None = None
    stripped: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    quipu_degraded: bool = False
    hank_degraded: bool = False
    #: True when no guard was configured. A guard that is down allows, and a guard that
    #: was never wired also allows — but only one of those is a deployment problem, and
    #: the record has to say which.
    hank_absent: bool = False
    quipu_latency_ms: int = 0
    hank_latency_ms: int = 0

    @property
    def cited(self) -> int:
        return len(self.quipu_cited)

    @property
    def utilisation(self) -> float | None:
        """Fraction of offered facts the model said it used.

        ``None`` when nothing was offered or the retriever does not label facts —
        deliberately not 0.0, which would read as "the model ignored the grounding".
        """
        if not self.quipu_facts:
            return None
        return len(self.quipu_cited) / len(self.quipu_facts)

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
        # Absent, not empty. summarise() turns this into quipu_absent.
        return Grounding(reason="no retriever configured")
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


def grounded_ids(world_view: WorldView) -> set[str]:
    """Fact ids retrieval put in front of the brain, read back off the world view.

    Facts are injected id-first — ``"unit:formers Formers; terraforms terrain"`` — so the id is
    the first token. That encoding is written in one place (``orchestrator.decide``) and has
    three readers: the citation guard's offered set, the plan block's "which citations did
    grounding *not* offer", and this. Kept here so the split lives once; a second copy is how a
    richer encoding would silently start reading wrong.

    Deliberately not the same thing as ``Grounding.fact_ids``: that is what retrieval returned,
    this is what the stored world view actually shows. They agree on the live path, and only
    this one is available to a replay reading a record back.
    """
    return {gid for gid in (_id_of(line) for line in (world_view.grounding or [])) if gid}


def _id_of(line: str) -> str:
    """Kept deliberately dumb. A richer encoding would need the contract to carry structured
    grounding, and that is a contract change rather than a reader's business."""
    return line.split(" ", 1)[0].strip() if line else ""


def summarise(
    grounding: Grounding,
    ruling: Ruling,
    guarded: bool,
    cited: Iterable[str] = (),
) -> Knowledge:
    """Fold both results into the record's provenance block.

    ``cited`` is the set of fact ids the brain said it relied on. Only ids that were
    actually offered are recorded: a model naming a fact it was never given is a
    hallucination, and laundering it into the provenance block would make the record
    lie about what informed the decision.
    """
    offered = list(grounding.fact_ids)
    offered_set = set(offered)
    used = [c for c in dict.fromkeys(cited) if c in offered_set]
    return Knowledge(
        quipu_hits=grounding.hits,
        quipu_facts=offered,
        quipu_cited=used,
        quipu_absent=grounding.reason == "no retriever configured",
        hank_verdict=ruling.verdict if guarded else None,
        stripped=list(ruling.stripped),
        advisories=list(ruling.advisories),
        quipu_degraded=grounding.degraded,
        hank_degraded=ruling.degraded,
        hank_absent=not guarded,
        quipu_latency_ms=grounding.latency_ms,
        hank_latency_ms=ruling.latency_ms,
    )


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _with_latency(grounding: Grounding, latency_ms: int) -> Grounding:
    """Stamp latency without rebuilding the object field by field.

    This used to enumerate fields, and it silently dropped ``fact_ids`` the moment that field
    was added — retrieval returned ids, the record showed none, and utilisation read as
    unmeasurable on every decision. ``replace`` cannot forget a field, which is the whole
    reason to use it here.
    """
    return replace(grounding, latency_ms=latency_ms)


def _with_ruling_latency(ruling: Ruling, latency_ms: int) -> Ruling:
    """Same reasoning as ``_with_latency`` — never enumerate fields to copy."""
    return replace(ruling, latency_ms=latency_ms)
