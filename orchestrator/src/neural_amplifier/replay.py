"""Replay as regression — ``docs/observability.md`` §5 and step 7.

The decision record is content-addressed but does not *contain* its input: it
carries `world_view_hash` and nothing else. That is the right shape — records
stay small and the hash makes "identical input?" a string comparison — but it
means a log alone cannot be replayed. Something has to keep the bytes.

:class:`WorldViewStore` is that something. Content-addressed by the same hash
the record already carries, so storage dedupes across turns for free and a
record and its input can never be mismatched.

Replaying then means: take a log, fetch each input, run it through a *changed*
orchestrator with no game running, and diff. Determinism only holds for the
scripted brain — a real-model run gets the weaker assertions instead (same
surfaces fired, still legal, no new degradation), which is why
:class:`Comparison` reports those separately rather than collapsing to a bool.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .contract import WorldView
from .decisions import DecisionRecord, world_view_hash
from .orchestrator import Orchestrator


class WorldViewStore:
    """Content-addressed world views, one JSON file per distinct input.

    Keyed by the record's own ``world_view_hash``, so a stored input cannot
    drift from the record that references it — if the bytes change, so does the
    key, and the old record simply points at the old file.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        # "sha256:abc…" is not a legal filename on every platform.
        return self.root / f"{digest.replace(':', '-')}.json"

    def put(self, world_view: WorldView) -> str:
        payload = world_view.model_dump(mode="json")
        digest = world_view_hash(payload)
        path = self._path(digest)
        if not path.exists():  # content-addressed: identical input, identical file
            path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        return digest

    def get(self, digest: str) -> WorldView | None:
        path = self._path(digest)
        if not path.exists():
            return None
        return WorldView.model_validate(json.loads(path.read_text(encoding="utf-8")))

    # Deliberately no ``write`` method: the store needs the *world view*, which
    # a record does not carry. Omitting it makes the store fail the ``Sink``
    # protocol, so wiring it into ``sinks=[...]`` raises at construction rather
    # than silently storing nothing for a whole run.


@dataclass
class Divergence:
    """One decision that came out differently the second time."""

    turn: int
    surface_id: str | None
    before: list[str]
    after: list[str]
    reason: str


@dataclass
class Comparison:
    """The result of replaying a log.

    Kept as separate counts rather than a single verdict because the two run
    kinds support different claims: a scripted replay must match exactly, and a
    real-model replay can only be held to legality and coverage.
    """

    total: int = 0
    matched: int = 0
    diverged: list[Divergence] = field(default_factory=list)
    #: Inputs the log references that the store does not have. Replay skipped
    #: them, so they are not evidence of anything — including of success.
    missing_inputs: int = 0
    #: Decisions that degraded on replay but not originally. A regression even
    #: when the chosen action happens to line up.
    new_degradations: int = 0
    surfaces_before: set[str] = field(default_factory=set)
    surfaces_after: set[str] = field(default_factory=set)

    @property
    def deterministic(self) -> bool:
        """Every replayed decision matched. Only meaningful for scripted runs."""
        return not self.diverged and self.replayed > 0

    @property
    def replayed(self) -> int:
        return self.total - self.missing_inputs

    @property
    def consistent(self) -> bool:
        """The weaker claim a real-model run can support: nothing newly broke
        and the same surfaces were exercised."""
        return self.new_degradations == 0 and self.surfaces_before == self.surfaces_after

    def summary(self) -> dict[str, object]:
        return {
            "decisions": self.total,
            "replayed": self.replayed,
            "matched": self.matched,
            "diverged": len(self.diverged),
            "missing_inputs": self.missing_inputs,
            "new_degradations": self.new_degradations,
            "deterministic": self.deterministic,
            "consistent": self.consistent,
            "surfaces_lost": sorted(self.surfaces_before - self.surfaces_after),
            "surfaces_gained": sorted(self.surfaces_after - self.surfaces_before),
        }


def _chosen(record: DecisionRecord) -> list[str]:
    return [str(c.get("action_id")) for c in record.chosen]


def replay(
    records: Iterable[DecisionRecord],
    store: WorldViewStore,
    orchestrator: Orchestrator,
) -> Comparison:
    """Re-run a recorded log through a (possibly changed) orchestrator.

    No game, no adapter, no network — which is what makes this usable as a
    regression gate rather than a nightly.
    """
    out = Comparison()
    for before in records:
        out.total += 1
        if before.surface_id:
            out.surfaces_before.add(before.surface_id)

        world_view = store.get(before.world_view_hash)
        if world_view is None:
            out.missing_inputs += 1
            continue

        after = orchestrator.decide(world_view).record
        if after.surface_id:
            out.surfaces_after.add(after.surface_id)
        if after.degraded and not before.degraded:
            out.new_degradations += 1

        if _chosen(before) == _chosen(after):
            out.matched += 1
        else:
            out.diverged.append(
                Divergence(
                    turn=before.turn,
                    surface_id=before.surface_id,
                    before=_chosen(before),
                    after=_chosen(after),
                    reason=after.degrade_reason or "different choice",
                )
            )
    return out


def stored(store: WorldViewStore) -> Iterator[WorldView]:
    """Every world view in the store, for fixture harvesting.

    A real run's inputs are the best fixtures available — recording one game
    gives the orchestrator suite material no hand-written fixture would.
    """
    for path in sorted(store.root.glob("sha256-*.json")):
        yield WorldView.model_validate(json.loads(path.read_text(encoding="utf-8")))
