"""Coverage as the assertion surface, not a report nobody reads."""

from __future__ import annotations

from pathlib import Path

from neural_amplifier import surfaces
from neural_amplifier.brain import BrainError, ScriptedBrain
from neural_amplifier.contract import WorldView
from neural_amplifier.coverage import report
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.orchestrator import Orchestrator


def test_surface_ids_are_frozen_and_partitioned() -> None:
    """The three domains must not overlap — scope_for() would be ambiguous."""
    assert surfaces.BASE.isdisjoint(surfaces.UNIT)
    assert surfaces.BASE.isdisjoint(surfaces.FACTION)
    assert surfaces.UNIT.isdisjoint(surfaces.FACTION)
    assert surfaces.ALL == surfaces.BASE | surfaces.UNIT | surfaces.FACTION


def test_gap_list_is_a_subset_of_the_registry() -> None:
    """A dialog-only surface still has to be a known surface."""
    assert surfaces.NO_AI_PATH <= surfaces.ALL


def test_scope_matches_the_contract_vocabulary() -> None:
    assert surfaces.scope_for("base.production") == "base"
    assert surfaces.scope_for("unit.design") == "unit"
    assert surfaces.scope_for("faction.tech") == "turn"
    assert surfaces.scope_for("not.a.surface") is None


def test_coverage_counts_what_fired(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    orchestrator = Orchestrator(ScriptedBrain(), log=log)
    orchestrator.decide(thinker_base)
    orchestrator.decide(thinker_base)

    result = report(log.read())
    assert result.total == 2
    assert result.fired["base.production"] == 2
    assert "base.production" in result.covered()


def test_uncovered_surfaces_are_visible(thinker_base: WorldView, tmp_path: Path) -> None:
    """A surface implemented but never fired means the scenario is wrong or the
    hook is misplaced — invisible without this."""
    log = DecisionLog(tmp_path / "d.jsonl")
    Orchestrator(ScriptedBrain(), log=log).decide(thinker_base)

    result = report(log.read())
    assert "unit.design" in result.uncovered()
    assert "base.production" not in result.uncovered()


def test_a_fully_degraded_run_is_distinguishable_from_a_real_one(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """The failure tests miss: every turn fell back, the run completed, and
    every 'did it run?' assertion passes. degrade_rate is what catches it."""
    log = DecisionLog(tmp_path / "d.jsonl")
    orchestrator = Orchestrator(ScriptedBrain(raises=BrainError("down")), log=log)
    for _ in range(4):
        orchestrator.decide(thinker_base)

    result = report(log.read())
    assert result.total == 4
    assert result.degrade_rate == 1.0
    # ...and it still "played" every turn, which is exactly the trap.
    assert result.fired["base.production"] == 4


def test_healthy_run_has_zero_degradation(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    Orchestrator(ScriptedBrain(), log=log).decide(thinker_base)
    assert report(log.read()).degrade_rate == 0.0


def test_adherence_is_asserted_as_exactly_zero(thinker_base: WorldView, tmp_path: Path) -> None:
    from neural_amplifier.contract import Choice, Orders

    log = DecisionLog(tmp_path / "d.jsonl")
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1"), Choice(action_id="ghost")])])
    Orchestrator(brain, log=log).decide(thinker_base)

    result = report(log.read())
    assert result.adherence_violations == 1
    assert result.adherent is False


def test_unknown_surface_id_is_flagged(thinker_base: WorldView, tmp_path: Path) -> None:
    """An adapter emitting an ID outside the frozen registry is a bug — most
    likely a rename that silently invalidates historical coverage."""
    log = DecisionLog(tmp_path / "d.jsonl")
    typo = thinker_base.model_copy(update={"surface_id": "base.producton"})
    Orchestrator(ScriptedBrain(), log=log).decide(typo)

    assert report(log.read()).unknown_surface_ids == {"base.producton"}


def test_missing_surface_id_is_counted_not_ignored(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    anonymous = thinker_base.model_copy(update={"surface_id": None})
    Orchestrator(ScriptedBrain(), log=log).decide(anonymous)

    result = report(log.read())
    assert result.missing_surface_id == 1
    assert result.summary()["surfaces_fired"] == 0
