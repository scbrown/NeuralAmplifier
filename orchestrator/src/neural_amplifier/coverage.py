"""Coverage and health, computed from a decision log.

This is what turns ``docs/game-surface.md`` from a design document into a
measurement, and it is the assertion surface the harness uses instead of
bespoke test hooks (``docs/observability.md`` §5).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from . import fairness, surfaces
from .decisions import DecisionRecord


@dataclass
class Report:
    """Aggregate health of one run."""

    total: int = 0
    fired: Counter[str] = field(default_factory=Counter)
    degraded: int = 0
    adherence_violations: int = 0
    #: Choices dropped as repeats of one already made in the same answer. Not an adherence
    #: failure — the ids were legal — so it gets its own line rather than a ceiling to assert.
    #: A run where this climbs is a brain looping, which reads as healthy on every other number.
    repeated_actions: int = 0
    unknown_surface_ids: set[str] = field(default_factory=set)
    missing_surface_id: int = 0
    #: Decisions the brain was actually asked to make. `total` minus the surfaces
    #: `na.toml` hands to the engine.
    llm_decisions: int = 0
    #: Decisions handed to the engine by configuration — not failures, and reported separately
    #: so a deliberately narrow run is legible rather than looking like an absent brain.
    deterministic_decisions: int = 0
    handicaps: set[str] = field(default_factory=set)
    redacted_deltas: int = 0
    ungated_decisions: int = 0

    @property
    def degrade_rate(self) -> float:
        """Share of **LLM-tier** decisions that came from the fallback rather than the brain.

        A run of pure fallbacks completes and looks green; this is the number that catches it.
        Assert a ceiling in the harness.

        The denominator is `llm_decisions`, not `total`, and that is load-bearing now that
        surfaces can be switched off in `na.toml`. A deterministic-tier decision is one
        the brain was never asked for; counting it here would dilute the rate and let a run
        where the brain failed on every surface it *did* own read as healthy, simply because
        most surfaces were configured off.
        """
        return self.degraded / self.llm_decisions if self.llm_decisions else 0.0

    @property
    def adherent(self) -> bool:
        """Orders never referenced an action the engine didn't offer.

        Structurally guaranteed, so assert ``True`` — not "few violations".
        """
        return self.adherence_violations == 0

    @property
    def structural_handicaps(self) -> set[str]:
        """Advantages nobody selected. These are what a result has to defend."""
        return {h for h in self.handicaps if fairness.is_structural(h)}

    @property
    def fair_play(self) -> bool:
        """Whether this run supports an **unqualified** fair-play claim.

        True only when no handicap was in force for any decision — a human slot
        under unmodified rules. Difficulty-selected advantages still disqualify
        it: they make the result honest, not unqualified, and the win has to be
        reported as "won with N declared advantages" (``game-surface.md`` §5).
        """
        return not self.handicaps

    @property
    def fog_enforced(self) -> bool:
        """Whether every decision was fog-gated.

        False means at least one world view could not be gated: it arrived with
        no ``contacts``, or a delta named parties in a shape the gate cannot
        read. Either way a leaked pact could not have been detected. Not proof
        of a cheat — proof that we could not have seen one.
        """
        return self.ungated_decisions == 0

    def covered(self) -> set[str]:
        return set(self.fired)

    def uncovered(self) -> set[str]:
        """Known surfaces this run never exercised.

        Either the scenario is wrong or the hook is misplaced — both worth
        knowing, and invisible without this.
        """
        return set(surfaces.ALL) - self.covered()

    def summary(self) -> dict[str, object]:
        return {
            "decisions": self.total,
            "surfaces_fired": len(self.fired),
            "surfaces_known": len(surfaces.ALL),
            "llm_decisions": self.llm_decisions,
            "deterministic_decisions": self.deterministic_decisions,
            "degrade_rate": round(self.degrade_rate, 4),
            "adherence_violations": self.adherence_violations,
            "repeated_actions": self.repeated_actions,
            "unknown_surface_ids": sorted(self.unknown_surface_ids),
            "missing_surface_id": self.missing_surface_id,
            "handicaps": sorted(self.handicaps),
            "structural_handicaps": sorted(self.structural_handicaps),
            "fair_play": self.fair_play,
            "redacted_deltas": self.redacted_deltas,
            "fog_enforced": self.fog_enforced,
        }


def report(records: Iterable[DecisionRecord]) -> Report:
    """Aggregate a decision stream."""
    out = Report()
    for record in records:
        out.total += 1
        if record.tier == "llm":
            out.llm_decisions += 1
        else:
            out.deterministic_decisions += 1
        if record.degraded:
            out.degraded += 1
        out.adherence_violations += record.adherence_violations
        out.repeated_actions += record.repeated_actions
        out.handicaps.update(record.fairness_profile)
        out.redacted_deltas += record.redacted_deltas
        if not record.fog_enforced:
            out.ungated_decisions += 1

        surface_id = record.surface_id
        if surface_id is None:
            out.missing_surface_id += 1
            continue
        out.fired[surface_id] += 1
        if not surfaces.is_known(surface_id):
            out.unknown_surface_ids.add(surface_id)
    return out
