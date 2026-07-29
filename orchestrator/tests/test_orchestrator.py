"""The decision loop — degradation, adherence, and the record it must emit.

The single most valuable property here: a run that degraded every turn must be
*distinguishable* from a run that played. Without that, a brain that was never
present completes successfully and looks green.
"""

from __future__ import annotations

from pathlib import Path

from neural_amplifier.brain import BrainError, ScriptedBrain
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.orchestrator import Orchestrator


def test_happy_path_returns_the_brains_choice(thinker_base: WorldView) -> None:
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1", reason="economy first")])])
    result = Orchestrator(brain).decide(thinker_base)

    assert [c.action_id for c in result.orders.choices] == ["a1"]
    assert result.orders.degraded is False
    assert result.record.degraded is False
    assert result.record.degrade_reason is None


def test_brain_error_degrades_to_end_turn(thinker_base: WorldView) -> None:
    brain = ScriptedBrain(raises=BrainError("timeout"))
    result = Orchestrator(brain).decide(thinker_base)

    assert [c.action_id for c in result.orders.choices] == ["a4"]
    assert result.orders.degraded is True
    assert result.record.degrade_reason == "timeout"


def test_unexpected_exception_still_degrades_rather_than_stalling(
    thinker_base: WorldView,
) -> None:
    """Invariant #9: a broken brain never stalls the game."""
    brain = ScriptedBrain(raises=ValueError("kaboom"))
    result = Orchestrator(brain).decide(thinker_base)

    assert result.orders.degraded is True
    assert "ValueError" in (result.record.degrade_reason or "")


def test_all_illegal_choices_degrade_and_are_counted(thinker_base: WorldView) -> None:
    """A reply that survives nothing is indistinguishable from a stall, so it
    counts as degradation — and the violation is recorded."""
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="not_a_real_action")])])
    result = Orchestrator(brain).decide(thinker_base)

    assert result.orders.degraded is True
    assert result.record.adherence_violations == 1
    assert [c.action_id for c in result.orders.choices] == ["a4"]


def test_partly_illegal_orders_keep_the_legal_part(thinker_base: WorldView) -> None:
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1"), Choice(action_id="bogus")])])
    result = Orchestrator(brain).decide(thinker_base)

    assert [c.action_id for c in result.orders.choices] == ["a1"]
    assert result.orders.degraded is False
    assert result.record.adherence_violations == 1


def test_degrading_without_end_turn_uses_a_legal_action(glsmac_thin: WorldView) -> None:
    """GLSMAC has no end_turn; the fallback must still be legal."""
    brain = ScriptedBrain(raises=BrainError("down"))
    result = Orchestrator(brain).decide(glsmac_thin)

    assert [c.action_id for c in result.orders.choices] == ["m1"]
    assert result.orders.degraded is True


def test_record_carries_surface_trace_and_fairness(thinker_base: WorldView) -> None:
    record = Orchestrator(ScriptedBrain()).decide(thinker_base).record

    assert record.surface_id == "base.production"
    assert record.scope == "base"
    assert record.trace_id is not None
    assert record.fairness_profile == ["retool_penalty", "tech_cost_factor"]
    assert record.action_space_size == 4


def test_empty_fairness_profile_when_engine_declares_none(glsmac_thin: WorldView) -> None:
    """An empty profile is a claim — it must come from the world view, not a
    default that hides an undeclared handicap."""
    assert Orchestrator(ScriptedBrain()).decide(glsmac_thin).record.fairness_profile == []


def test_world_view_hash_is_stable_across_identical_inputs(thinker_base: WorldView) -> None:
    """Determinism diffing and replay both key on this being a pure function
    of the input."""
    a = Orchestrator(ScriptedBrain()).decide(thinker_base).record
    b = Orchestrator(ScriptedBrain()).decide(thinker_base).record
    assert a.world_view_hash == b.world_view_hash


def test_world_view_hash_changes_when_input_changes(thinker_base: WorldView) -> None:
    a = Orchestrator(ScriptedBrain()).decide(thinker_base).record
    moved = thinker_base.model_copy(update={"turn": 43})
    b = Orchestrator(ScriptedBrain()).decide(moved).record
    assert a.world_view_hash != b.world_view_hash


def test_every_decision_writes_exactly_one_record(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "decisions.jsonl")
    orchestrator = Orchestrator(ScriptedBrain(), log=log)

    orchestrator.decide(thinker_base)
    orchestrator.decide(thinker_base)

    assert len(list(log.read())) == 2
