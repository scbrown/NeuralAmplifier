"""Fast, deterministic scoring for engine-emitted strategic outcome events.

The game is expensive to run; reading what it already observed must not be.  This module owns
the engine-neutral event vocabulary and reduces a JSONL stream to falsifiable domain gates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SCHEMA = "na.outcome.v1"
DOMAINS = ("unit_strategy", "defense", "attack", "sea_vehicles", "sea_bases")
COMPARATORS = {"at_least", "at_most"}


class DomainEvalError(ValueError):
    """The evidence or definition cannot support a verdict."""


@dataclass(frozen=True)
class Gate:
    metric: str
    comparator: Literal["at_least", "at_most"]
    target: float


@dataclass(frozen=True)
class GateResult:
    metric: str
    comparator: str
    target: float
    observed: float

    @property
    def passed(self) -> bool:
        if self.comparator == "at_least":
            return self.observed >= self.target
        return self.observed <= self.target


@dataclass(frozen=True)
class DomainResult:
    domain: str
    gates: tuple[GateResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(gate.passed for gate in self.gates)


def load_events(path: Path, *, faction_id: int | None = None) -> list[dict[str, Any]]:
    """Load outcome events, refusing corrupt or mixed-schema evidence."""
    if not path.is_file():
        raise DomainEvalError(f"outcome log does not exist: {path}")
    events: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DomainEvalError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise DomainEvalError(f"{path}:{number}: event must be an object")
        if event.get("schema") != SCHEMA:
            raise DomainEvalError(
                f"{path}:{number}: schema must be {SCHEMA!r}, got {event.get('schema')!r}"
            )
        if not isinstance(event.get("event"), str) or not isinstance(event.get("turn"), int):
            raise DomainEvalError(f"{path}:{number}: event and integer turn are required")
        if faction_id is None or event.get("faction_id") == faction_id:
            events.append(event)
    if not events:
        suffix = f" for faction {faction_id}" if faction_id is not None else ""
        raise DomainEvalError(f"outcome log contains no events{suffix}: {path}")
    return events


def metrics(events: list[dict[str, Any]]) -> dict[str, float]:
    """Reduce events to the stable metric vocabulary used by domain definitions."""

    def count(name: str) -> int:
        return sum(event["event"] == name for event in events)

    combats = [event for event in events if event["event"] == "combat.resolved"]
    defense = [event for event in combats if event.get("posture") == "defense"]
    attack = [event for event in combats if event.get("posture") == "attack"]
    founded = {
        str(event["base_id"])
        for event in events
        if event["event"] == "base.founded" and event.get("terrain") == "sea"
    }
    lost = {
        str(event["base_id"])
        for event in events
        if event["event"] == "base.lost" and event.get("terrain") == "sea"
    }

    def rate(rows: list[dict[str, Any]], outcome: str) -> float:
        return sum(row.get("outcome") == outcome for row in rows) / len(rows) if rows else 0.0

    unit_actions = [event for event in events if event["event"] == "unit.action"]
    classes = {str(event["unit_class"]) for event in unit_actions if event.get("unit_class")}
    return {
        "unit_actions": float(len(unit_actions)),
        "active_unit_classes": float(len(classes)),
        "defensive_engagements": float(len(defense)),
        "defensive_win_rate": rate(defense, "won"),
        "offensive_engagements": float(len(attack)),
        "offensive_win_rate": rate(attack, "won"),
        "enemy_units_killed": float(count("unit.killed")),
        "own_units_lost": float(count("unit.lost")),
        "naval_units_produced": float(count("naval.production")),
        "naval_moves": float(count("naval.movement")),
        "naval_actions": float(count("naval.action")),
        "sea_bases_founded": float(len(founded)),
        "sea_bases_survived": float(len(founded - lost)),
        "sea_base_survival_rate": len(founded - lost) / len(founded) if founded else 0.0,
    }


def load_definitions(path: Path) -> dict[str, tuple[Gate, ...]]:
    """Load and validate domain gates before looking at a result."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DomainEvalError(f"cannot read eval definitions {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != set(DOMAINS):
        raise DomainEvalError(f"definitions must contain exactly: {', '.join(DOMAINS)}")
    known = metrics([{"schema": SCHEMA, "event": "noop", "turn": 0}])
    definitions: dict[str, tuple[Gate, ...]] = {}
    for domain in DOMAINS:
        rows = raw[domain]
        if not isinstance(rows, list) or not rows:
            raise DomainEvalError(f"{domain}: at least one gate is required")
        gates: list[Gate] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("metric") not in known:
                raise DomainEvalError(f"{domain}: unknown metric {row.get('metric')!r}")
            if row.get("comparator") not in COMPARATORS:
                raise DomainEvalError(f"{domain}: comparator must be at_least or at_most")
            if not isinstance(row.get("target"), int | float):
                raise DomainEvalError(f"{domain}: target must be numeric")
            gates.append(Gate(row["metric"], row["comparator"], float(row["target"])))
        definitions[domain] = tuple(gates)
    return definitions


def evaluate(
    events: list[dict[str, Any]], definitions: dict[str, tuple[Gate, ...]]
) -> tuple[DomainResult, ...]:
    observed = metrics(events)
    return tuple(
        DomainResult(
            domain,
            tuple(GateResult(g.metric, g.comparator, g.target, observed[g.metric]) for g in gates),
        )
        for domain, gates in definitions.items()
    )
