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

Tier = Literal["deterministic", "llm"]


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
    hank_verdict: str | None = None
    stripped: list[str] = Field(default_factory=list)
    advisories: list[str] = Field(default_factory=list)
    quipu_degraded: bool = False
    hank_degraded: bool = False
    quipu_latency_ms: int = 0
    hank_latency_ms: int = 0


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

    #: What Quipu and Hank contributed (``docs/observability.md`` §7).
    knowledge: KnowledgeBlock = Field(default_factory=KnowledgeBlock)

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
