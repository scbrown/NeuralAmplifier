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


def test_history_carries_the_tier_that_made_each_choice() -> None:
    """`tier` is what stops history becoming deference to a choice nobody made.

    Re-reading your own earlier reasoning is a different act from reading the deterministic
    tier's default: the first has an argument you can still weigh, the second has none. Measured
    (na-61c.2): with three turns of `llm` history behind it, a decision that continued its prior
    choice 3 times in 10 continued it 10 times in 10.
    """
    view = WorldView.model_validate(
        {
            "engine": "thinker",
            "scope": "base",
            "turn": 35,
            "faction": "University",
            "history": [
                {"turn": 33, "item": "Network Node", "tier": "llm"},
                {"turn": 34, "item": "Network Node", "tier": "deterministic"},
                {"turn": 34, "item": "Recreation Commons"},
            ],
        }
    )
    assert view.history is not None
    assert [h.item for h in view.history][0] == "Network Node"
    assert [h.tier for h in view.history] == ["llm", "deterministic", None]


def test_absent_history_is_not_an_empty_history() -> None:
    """An adapter that does not track builds is distinguishable from a base on its first turn.

    The first is a gap to fix, the second is a decision genuinely made from nothing, and a
    brain told "you have never chosen anything here" when the truth is "nobody recorded it"
    would draw the wrong conclusion about its own consistency.
    """
    bare = WorldView(engine="thinker", scope="base", turn=1, faction="University")
    fresh = WorldView(engine="thinker", scope="base", turn=1, faction="University", history=[])

    assert bare.history is None
    assert fresh.history == []
