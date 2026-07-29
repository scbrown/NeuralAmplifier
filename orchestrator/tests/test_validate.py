"""Action-space adherence — VISION §4's anti-hallucination guarantee."""

from __future__ import annotations

from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.validate import validate


def test_legal_choices_pass_through(thinker_base: WorldView) -> None:
    orders = Orders(choices=[Choice(action_id="a1"), Choice(action_id="a4")])
    result = validate(orders, thinker_base)
    assert [c.action_id for c in result.kept] == ["a1", "a4"]
    assert result.adherent


def test_invented_action_is_dropped_and_counted(thinker_base: WorldView) -> None:
    """An action the engine never offered must never reach the adapter — and
    must be counted, because that count is the invariant the harness asserts."""
    orders = Orders(choices=[Choice(action_id="a1"), Choice(action_id="nuke_everything")])
    result = validate(orders, thinker_base)
    assert [c.action_id for c in result.kept] == ["a1"]
    assert result.unknown == ["nuke_everything"]
    assert not result.adherent


def test_duplicate_choices_are_dropped(thinker_base: WorldView) -> None:
    orders = Orders(choices=[Choice(action_id="a1"), Choice(action_id="a1")])
    result = validate(orders, thinker_base)
    assert [c.action_id for c in result.kept] == ["a1"]
    assert result.duplicates == ["a1"]
    # A repeat is sloppy, not illegal — adherence still holds.
    assert result.adherent


def test_choice_order_is_preserved(thinker_base: WorldView) -> None:
    """The adapter applies choices in sequence, so reordering would silently
    change what the turn does."""
    orders = Orders(choices=[Choice(action_id="a3"), Choice(action_id="a1")])
    assert [c.action_id for c in validate(orders, thinker_base).kept] == ["a3", "a1"]


def test_reasons_survive_validation(thinker_base: WorldView) -> None:
    """The reason is the legibility payload — dropping it would gut the pitch."""
    orders = Orders(choices=[Choice(action_id="a1", reason="economy first")])
    assert validate(orders, thinker_base).kept[0].reason == "economy first"
