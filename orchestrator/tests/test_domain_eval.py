from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_amplifier.domain_eval import (
    SCHEMA,
    DomainEvalError,
    evaluate,
    load_definitions,
    load_events,
    metrics,
)


def event(name: str, **extra: object) -> dict[str, object]:
    return {"schema": SCHEMA, "event": name, "turn": 10, "faction_id": 7, **extra}


def test_five_domains_score_from_one_tiny_replay() -> None:
    events = [
        event("unit.action", unit_class="infantry"),
        event("combat.resolved", posture="defense", outcome="won"),
        event("combat.resolved", posture="attack", outcome="won"),
        event("unit.killed"),
        event("naval.production"),
        event("naval.movement"),
        event("naval.action"),
        event("base.founded", base_id=81, terrain="sea"),
    ]
    definitions = load_definitions(Path(__file__).parents[2] / "evals" / "domains.json")
    results = evaluate(events, definitions)
    assert len(results) == 5
    assert all(result.passed for result in results)
    assert metrics(events)["sea_base_survival_rate"] == 1.0


def test_a_missing_domain_observable_fails_instead_of_disappearing() -> None:
    definitions = load_definitions(Path(__file__).parents[2] / "evals" / "domains.json")
    results = {result.domain: result for result in evaluate([event("unit.action")], definitions)}
    assert not results["sea_vehicles"].passed
    assert not results["sea_bases"].passed


def test_zero_is_a_valid_engine_unit_class() -> None:
    assert metrics([event("unit.action", unit_class=0)])["active_unit_classes"] == 1.0


def test_filtering_refuses_a_faction_with_no_evidence(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(event("unit.action")) + "\n", encoding="utf-8")
    with pytest.raises(DomainEvalError, match="no events for faction 6"):
        load_events(path, faction_id=6)


def test_malformed_line_refuses_the_whole_verdict(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(event("unit.action")) + "\nnot-json\n", encoding="utf-8")
    with pytest.raises(DomainEvalError, match="invalid JSON"):
        load_events(path)
