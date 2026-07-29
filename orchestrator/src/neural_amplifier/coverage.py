"""Coverage and health, computed from a decision log.

This is what turns ``docs/game-surface.md`` from a design document into a
measurement, and it is the assertion surface the harness uses instead of
bespoke test hooks (``docs/observability.md`` §5).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from . import surfaces
from .decisions import DecisionRecord


@dataclass
class Report:
    """Aggregate health of one run."""

    total: int = 0
    fired: Counter[str] = field(default_factory=Counter)
    degraded: int = 0
    adherence_violations: int = 0
    unknown_surface_ids: set[str] = field(default_factory=set)
    missing_surface_id: int = 0

    @property
    def degrade_rate(self) -> float:
        """Share of decisions that came from the fallback rather than the brain.

        A run of pure fallbacks completes and looks green; this is the number
        that catches it. Assert a ceiling in the harness.
        """
        return self.degraded / self.total if self.total else 0.0

    @property
    def adherent(self) -> bool:
        """Orders never referenced an action the engine didn't offer.

        Structurally guaranteed, so assert ``True`` — not "few violations".
        """
        return self.adherence_violations == 0

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
            "degrade_rate": round(self.degrade_rate, 4),
            "adherence_violations": self.adherence_violations,
            "unknown_surface_ids": sorted(self.unknown_surface_ids),
            "missing_surface_id": self.missing_surface_id,
        }


def report(records: Iterable[DecisionRecord]) -> Report:
    """Aggregate a decision stream."""
    out = Report()
    for record in records:
        out.total += 1
        if record.degraded:
            out.degraded += 1
        out.adherence_violations += record.adherence_violations

        surface_id = record.surface_id
        if surface_id is None:
            out.missing_surface_id += 1
            continue
        out.fired[surface_id] += 1
        if not surfaces.is_known(surface_id):
            out.unknown_surface_ids.add(surface_id)
    return out
