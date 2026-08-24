"""The decision record — the atom of ``docs/observability.md``.

One structured event per decision point. Coverage, determinism diffing, replay,
and the ops signals are all aggregations or projections of this record, so it is
written once, append-only, as JSONL.

Field names are deliberately OpenTelemetry-shaped: the OTel exporter (layer 2)
is a projection of these events, not a translation of them, and both layers are
fed from the single :meth:`DecisionLog.write` call so they cannot disagree.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: `deferred` is a third thing, not a flavour of either (na-7bk). `deterministic` says nobody was
#: asked or nobody answered; `deferred` says the agent read the world view and chose to answer
#: later, through door 2. Both hand the engine its own pick, and collapsing them would make a
#: working mechanism indistinguishable from an absent brain in `degrade_rate` — the one number
#: that exists to catch a run where nothing was thinking.
#: `queued` is the fourth, and it is not a flavour of the other three either (na-7bk slice 2). No
#: brain was asked for this decision, so it is not `llm`; an agent chose the answer deliberately
#: and said in advance what would invalidate it, so it is not `deterministic`. Keeping it distinct
#: is what makes the only question worth asking about the mechanism answerable afterwards: how
#: often did a standing answer stand, and how often was it overtaken by the board?
#: `plan` is the fifth (na-7bk): an answer from the bulk-turn table the agent installed for
#: exactly this turn. Not `queued` — nothing conditional is standing, the table dies with its
#: turn — and the bead requires the distinction explicitly, so replay can tell strategy-driven
#: answers from agent-driven ones.
Tier = Literal["deterministic", "llm", "deferred", "queued", "plan"]


def world_view_hash(payload: dict[str, Any]) -> str:
    """Content address for a world view.

    ``sort_keys`` is load-bearing: the hash is how "identical input?" becomes a
    string comparison for determinism diffing and replay, so serialization must
    be stable.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


class Tokens(BaseModel):
    model_config = ConfigDict(extra="allow")

    input: int = 0
    output: int = 0
    cached: int = 0


class KnowledgeBlock(BaseModel):
    """Provenance: what the knowledge layer contributed to this decision.

    ``docs/observability.md`` §7 — this is what makes "the brain was told
    this" distinguishable from "the brain assumed this".
    """

    model_config = ConfigDict(extra="allow")

    quipu_hits: int = 0
    #: Ids of the facts offered to the model, and the subset it said it used. The gap
    #: between them is the only evidence retrieval mattered — ``quipu_hits`` counts what
    #: was offered, not what was read.
    quipu_facts: list[str] = Field(default_factory=list)
    quipu_cited: list[str] = Field(default_factory=list)
    #: No retriever / no guard was configured, as distinct from one that ran and failed
    #: (``*_degraded``) or ran and found nothing (``quipu_hits == 0``).
    quipu_absent: bool = False
    hank_absent: bool = False

    @property
    def utilisation(self) -> float | None:
        """Fraction of offered facts the model said it used.

        ``None`` when nothing was offered or the retriever does not label its facts —
        deliberately not 0.0, which reads as "the model ignored the grounding".
        """
        if not self.quipu_facts:
            return None
        return len(self.quipu_cited) / len(self.quipu_facts)

    hank_verdict: str | None = None
    stripped: list[str] = Field(default_factory=list)
    advisories: list[str] = Field(default_factory=list)
    quipu_degraded: bool = False
    hank_degraded: bool = False
    quipu_latency_ms: int = 0
    hank_latency_ms: int = 0


class PlanBlock(BaseModel):
    """What the standing plan did to this decision.

    The same measurement logic as :class:`KnowledgeBlock`, for the same reason. A directive
    mechanism that is never read is worse than none — it costs prompt tokens on every decision
    and buys the *appearance* of strategy. These fields are how that stops being an opinion:

    * ``in_force`` against ``followed`` gives an attention rate, exactly as offered-vs-cited
      gives grounding utilisation.
    * ``overrode`` gives an override rate per directive. A priority-7 plan overridden on every
      decision was mispriced; one never overridden may be costing more than it admits.
    * ``unmeasurable`` counts directives whose metric this world view did not report. Non-zero
      is an adapter gap, and it must not be confused with a directive that is merely unsatisfied.
    * ``entities_cited`` catches the citations grounding utilisation deliberately cannot see.
    """

    model_config = ConfigDict(extra="allow")

    in_force: list[str] = Field(default_factory=list)
    followed: list[str] = Field(default_factory=list)
    overrode: list[str] = Field(default_factory=list)
    #: Directives in force whose metric was absent from this world view — cannot be checked
    #: here, as distinct from checked and failing.
    unmeasurable: list[str] = Field(default_factory=list)
    #: Directives in force that this world view says are not satisfied.
    unsatisfied: list[str] = Field(default_factory=list)
    #: Datalinks ids this decision cited that only a directive had shown it — offered through
    #: ``directives[].directive.entities``, absent from ``grounding``.
    #:
    #: These are recorded here rather than folded into ``quipu_cited`` because they are not
    #: evidence about retrieval: nothing was retrieved for them, so counting them would push
    #: ``utilisation`` above 1.0 and make a retrieval metric move whenever the plan changed
    #: shape. But dropping them silently is how the plan's own contribution stayed invisible
    #: — a decision reasoning from an entity the plan surfaced looked identical to one that
    #: read nothing (na-zgz).
    entities_cited: list[str] = Field(default_factory=list)
    #: Ids this decision issued, and why any were refused.
    issued: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    #: Actions that would have broken a currently satisfied directive, as ``action_id:id``.
    conflicts: list[str] = Field(default_factory=list)
    #: Directives in force that this decision was NOT shown, because relevance selection capped
    #: the list. Recorded because a silent cap is what makes a plan look served when it was never
    #: read — with hundreds of directives in a late game, "not mentioned" and "never offered" are
    #: completely different problems and only this distinguishes them.
    not_shown: list[str] = Field(default_factory=list)
    #: No directive store was configured, as distinct from one that is simply empty.
    plan_absent: bool = False

    @property
    def attention(self) -> float | None:
        """Fraction of in-force directives this decision said it acted on.

        ``None`` when none were in force — deliberately not 0.0, which reads as "the model
        ignored the plan".
        """
        if not self.in_force:
            return None
        return len(self.followed) / len(self.in_force)


class DecisionRecord(BaseModel):
    """One decision. See ``docs/observability.md`` §2."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = "0.1"

    trace_id: str | None = None
    game_id: str | None = None
    turn: int
    year: int | None = None
    faction: str
    engine: str

    surface_id: str | None = None
    scope: str
    tier: Tier

    world_view_hash: str
    action_space_size: int
    chosen: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None

    #: Whether this came from the brain or the safe fallback. The single most
    #: valuable field for testing — a run of pure fallbacks otherwise completes
    #: and looks green (``docs/observability.md`` §5.4).
    degraded: bool = False
    degrade_reason: str | None = None

    #: Rule asymmetries in force, so a result stays interpretable later. An
    #: empty list is the claim "won under unmodified rules".
    fairness_profile: list[str] = Field(default_factory=list)

    #: Deltas the fog gate removed before the brain saw them, and whether it
    #: could run at all (``docs/headless-harness.md`` §4.2). A leaking adapter
    #: is otherwise invisible — the run just looks unusually well-informed.
    redacted_deltas: int = 0
    fog_enforced: bool = True

    #: Orders that referenced an action the engine never offered. Structurally
    #: impossible by design, so any non-zero value is a broken invariant.
    adherence_violations: int = 0

    #: Choices dropped for naming an action already chosen in the same answer.
    #: Kept apart from ``adherence_violations`` deliberately: a repeat is legal
    #: — the id was offered — so folding it in would make the one number that
    #: means "broken invariant" fire on a brain that is merely sloppy. It is
    #: still worth counting, because a brain repeating one id fifty times is a
    #: model looping, and without this the record of that answer is identical
    #: to the record of a clean one-choice turn.
    repeated_actions: int = 0

    #: Times this decision was re-asked with the reason attached after every
    #: choice was thrown out. A successful repair was otherwise invisible: the
    #: degrade reason names repair attempts only when they *failed*, so a
    #: decision that took two round trips to land read exactly like one that
    #: landed first time — and that difference is a turn the game spent waiting.
    repairs: int = 0

    #: ``world_view_hash`` of each augmented view the brain was re-asked from,
    #: in order. The store is written once, before the repair loop, so these are
    #: what make the second prompt reconstructible; without them the view the
    #: brain actually answered from existed for one call and nothing held the
    #: bytes. Empty when nothing was repaired, and also when no store is
    #: configured — which is why ``repairs`` is counted separately.
    #:
    #: Deliberately not folded into ``world_view_hash``, which stays this
    #: decision's *input*. A replay starts from the original and regenerates its
    #: own advisories: a changed guard producing a different second prompt is a
    #: divergence worth seeing, not one to hide by replaying the recorded one.
    repair_inputs: list[str] = Field(default_factory=list)

    #: What Quipu and Hank contributed (``docs/observability.md`` §7).
    knowledge: KnowledgeBlock = Field(default_factory=KnowledgeBlock)

    #: The turn-boundary grounding evidence this decision is bound to, as the reference Yupana
    #: verifies: ``{scope, grounding_id, faction_id, worldview_sha256}``
    #: (``grounding_evidence.py``).
    #:
    #: Deliberately a reference and not the evidence. ``knowledge`` already says what retrieval
    #: *contributed*; this says what can be independently *checked*, by a component that was not
    #: here when the decision was made and does not trust this record. The two are not
    #: redundant: ``knowledge`` is self-report, and the whole point of the enforcement contract
    #: is that a grounding claim must be falsifiable without it.
    #:
    #: ``None`` means no consultation was bound — no retriever, an unprimed turn, or a cache
    #: that could not be written. Absent is the honest answer and Yupana reads it as ``missing``;
    #: fabricating a reference here would produce ``unresolved`` downstream, which reads as
    #: corrupted evidence rather than as the absence it actually is.
    grounding: dict[str, str] | None = None

    #: What the standing plan contributed (``directives.py``).
    plan: PlanBlock = Field(default_factory=lambda: PlanBlock())

    model: str | None = None
    tokens: Tokens = Field(default_factory=Tokens)
    latency_ms: int | None = None


class DecisionLog:
    """Append-only JSONL writer — the record of truth for a run."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: DecisionRecord) -> None:
        line = record.model_dump_json(exclude_none=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read(self) -> Iterator[DecisionRecord]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield DecisionRecord.model_validate_json(line)
