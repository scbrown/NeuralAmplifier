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

    #: Retrieved facts and guard advisories the *orchestrator* injects before
    #: the brain call. Not adapter fields — an adapter never sets these; they
    #: live here so the brain sees one object (``knowledge.py``).
    grounding: list[str] | None = None
    advisories: list[str] | None = None

    #: Factions this faction has legitimately met. ``None`` means the adapter
    #: does not report contact, so the fog gate cannot run — see ``fog.py``.
    contacts: list[str] | None = None

    #: Datalinks entities this decision is *about*, as distinct from the entities it
    #: chooses *between*.
    #:
    #: Retrieval keys off action labels, which is right for a surface that picks among
    #: named things — ``base.production`` offers "Colony Pod" and the graph has a node
    #: called "Colony Pod". It is empty for a surface that asks a question about one
    #: entity: ``base.hurry`` offers "Hurry production" / "Do not hurry", neither of
    #: which is in any datalinks, so the whole surface retrieved zero facts and the
    #: brain decided it with no grounding at all (measured: 0.60 stability, the least
    #: stable surface we have).
    #:
    #: The subject is named here rather than dug out of ``economy`` because the
    #: orchestrator must not learn where a particular engine files it (invariant 2).
    #: The adapter knows what its decision is about; this is where it says so.
    subjects: list[str] | None = None

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
    #: Ids of the grounding facts the brain relied on. The only signal that retrieval
    #: influenced the answer rather than merely preceding it — a decision made with
    #: twelve facts all ignored is otherwise indistinguishable from one they drove.
    #: Ids not present in the offered set are discarded on the way to the record, so a
    #: hallucinated citation cannot inflate the measured utilisation.
    cited: list[str] = Field(
        default_factory=list,
        # The description matters: with structured output the model reads the JSON schema, and a
        # bare `array of strings` gets ignored no matter what the system prompt says. Measured —
        # explaining `cited` only in the system prompt left it empty on every run.
        description=(
            "Ids of the grounding facts that actually influenced this decision. Each entry in the"
            " world view's `grounding` list starts with its id, e.g. `unit:colony-pod`. Include a"
            " fact if reading it changed your assessment of an option — whether it supported the"
            " option you chose or helped you rule one out. Omit facts that made no difference, and"
            " never invent an id you were not given."
        ),
    )
