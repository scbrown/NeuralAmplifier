"""The contract must survive both engines, including what each one lacks."""

from __future__ import annotations

from neural_amplifier.contract import WorldView


def test_rich_thinker_world_view_parses(thinker_base: WorldView) -> None:
    assert thinker_base.engine == "thinker"
    assert thinker_base.scope == "base"
    assert thinker_base.surface_id == "base.production"
    assert thinker_base.action_ids() == {"a1", "a2", "a3", "a4"}


def test_missing_sections_are_absent_not_faked(glsmac_thin: WorldView) -> None:
    """An engine that lacks a system omits it — the orchestrator must not
    invent an economy GLSMAC does not have."""
    assert glsmac_thin.economy is None
    assert glsmac_thin.bases is None
    assert glsmac_thin.year is None


def test_fairness_separates_structural_from_difficulty(thinker_base: WorldView) -> None:
    """Only the structural set needs defending in a result — the difficulty
    ones are a user's choice."""
    fairness = thinker_base.fairness
    assert fairness is not None
    assert [h.id for h in fairness.structural()] == ["retool_penalty"]


def test_fallback_prefers_end_turn(thinker_base: WorldView) -> None:
    assert thinker_base.fallback_action_id() == "a4"


def test_fallback_without_end_turn_uses_first_legal_action(glsmac_thin: WorldView) -> None:
    """GLSMAC offers no end_turn; degrading must still produce something legal."""
    assert glsmac_thin.fallback_action_id() == "m1"


def test_fallback_with_empty_action_space_is_none() -> None:
    empty = WorldView(engine="thinker", scope="turn", turn=1, faction="GAIANS")
    assert empty.fallback_action_id() is None


def test_unmodelled_fields_are_preserved_not_rejected() -> None:
    """Engines grow faster than this file; a new field must not 400 the turn."""
    view = WorldView.model_validate(
        {
            "engine": "thinker",
            "scope": "turn",
            "turn": 1,
            "faction": "GAIANS",
            "some_future_section": {"a": 1},
        }
    )
    assert view.model_dump()["some_future_section"] == {"a": 1}
