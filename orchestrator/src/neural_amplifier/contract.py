"""The wire contract — ``docs/contract.md`` as Pydantic models.

The orchestrator speaks only these types and never learns which engine it is
driving (invariant #2). Fields an engine lacks are **omitted**, not faked, so
almost everything outside the core four is optional.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1"

Scope = Literal["turn", "unit", "base"]
Engine = Literal["thinker", "glsmac"]


class _Model(BaseModel):
    # Engines grow faster than this file; tolerate fields we don't model yet
    # rather than rejecting a world view outright.
    model_config = ConfigDict(extra="allow")


class Trace(_Model):
    """W3C trace context. The adapter is the trace root — the game is the root
    of the causality — and the orchestrator continues it."""

    traceparent: str | None = None


class Handicap(_Model):
    """One declared rule asymmetry (``docs/game-surface.md`` §5)."""

    id: str
    favours: Literal["self", "other", "config"] | None = None
    selected_by: Literal["difficulty", "structural"] | None = None
    detail: str | None = None


class Fairness(_Model):
    """The rule asymmetries in force for this faction.

    Policy is to **record, not neutralise**. An empty ``handicaps`` list is what
    backs an unqualified fair-play claim — never assert one without it.
    """

    slot: Literal["ai", "human"] | None = None
    difficulty: str | None = None
    handicaps: list[Handicap] = Field(default_factory=list)

    def structural(self) -> list[Handicap]:
        """Handicaps nobody selected — the set that needs defending in a result."""
        return [h for h in self.handicaps if h.selected_by == "structural"]


class Action(_Model):
    """One legal move. ``id`` is what orders reference."""

    id: str
    action: str


class WorldView(_Model):
    """Adapter → orchestrator. The complete input to a decision."""

    schema_version: str = SCHEMA_VERSION
    engine: Engine
    scope: Scope
    turn: int
    faction: str

    # Telemetry (optional, additive — an adapter that omits them still works,
    # it just cannot be measured).
    surface_id: str | None = None
    trace: Trace | None = None

    year: int | None = None
    fairness: Fairness | None = None
    action_space: list[Action] = Field(default_factory=list)
    memory: str | None = None

    # Engine-dependent sections, passed through to the prompt untouched.
    scores: dict[str, Any] | None = None
    economy: dict[str, Any] | None = None
    map: dict[str, Any] | None = None
    units: list[dict[str, Any]] | None = None
    bases: list[dict[str, Any]] | None = None
    deltas: list[dict[str, Any]] | None = None

    def action_ids(self) -> set[str]:
        return {a.id for a in self.action_space}

    def traceparent(self) -> str | None:
        """The adapter's W3C trace context, if it sent one.

        Tolerates a raw dict as well as a :class:`Trace`. ``model_copy`` skips
        validation, so a caller can legitimately hold a world view whose
        ``trace`` never went through the parser — and this is read *after* the
        decision, where an ``AttributeError`` would stall the game rather than
        degrade (invariant #9).
        """
        trace = self.trace
        if trace is None:
            return None
        if isinstance(trace, Trace):
            return trace.traceparent
        if isinstance(trace, dict):
            value = trace.get("traceparent")
            return value if isinstance(value, str) else None
        return None

    def fallback_action_id(self) -> str | None:
        """The safe degradation target: ``end_turn`` where present, else the
        first legal action, else nothing."""
        for a in self.action_space:
            if a.action == "end_turn":
                return a.id
        return self.action_space[0].id if self.action_space else None


class Choice(_Model):
    """One selected action, with the reasoning behind it."""

    action_id: str
    reason: str | None = None


class Orders(_Model):
    """Orchestrator → adapter."""

    schema_version: str = SCHEMA_VERSION
    choices: list[Choice] = Field(default_factory=list)
    notes: str | None = None
    degraded: bool = False
