"""The decision record and its JSONL log."""

from __future__ import annotations

from pathlib import Path

from neural_amplifier.decisions import DecisionLog, DecisionRecord, world_view_hash


def _record(**overrides: object) -> DecisionRecord:
    base: dict[str, object] = {
        "turn": 1,
        "faction": "GAIANS",
        "engine": "thinker",
        "scope": "base",
        "tier": "llm",
        "world_view_hash": "sha256:abc",
        "action_space_size": 3,
    }
    base.update(overrides)
    return DecisionRecord.model_validate(base)


def test_hash_is_order_independent() -> None:
    """Unsorted dict serialization would make every run look different — the
    classic silent breaker of both replay and determinism diffing."""
    assert world_view_hash({"a": 1, "b": 2}) == world_view_hash({"b": 2, "a": 1})


def test_hash_is_sensitive_to_values() -> None:
    assert world_view_hash({"a": 1}) != world_view_hash({"a": 2})


def test_log_round_trips(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "nested" / "d.jsonl")
    log.write(_record(surface_id="base.production"))
    log.write(_record(surface_id="unit.design", degraded=True))

    records = list(log.read())
    assert [r.surface_id for r in records] == ["base.production", "unit.design"]
    assert [r.degraded for r in records] == [False, True]


def test_log_is_append_only(tmp_path: Path) -> None:
    """Two orchestrators writing the same run must not truncate each other."""
    path = tmp_path / "d.jsonl"
    DecisionLog(path).write(_record())
    DecisionLog(path).write(_record())
    assert len(list(DecisionLog(path).read())) == 2


def test_reading_a_missing_log_is_empty_not_an_error(tmp_path: Path) -> None:
    assert list(DecisionLog(tmp_path / "absent.jsonl").read()) == []


def test_degraded_defaults_to_false() -> None:
    """The field that catches silent degradation must never default to 'fine'
    in a way that hides a fallback."""
    assert _record().degraded is False
    assert _record().adherence_violations == 0
