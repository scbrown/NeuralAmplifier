"""Directives: measurable standing intent, and the trade-offs that make a priority usable.

The mechanism exists because a long-horizon decision's conclusion used to die with its response —
the next ``base.production`` call knew nothing about the tech path just chosen. What these tests
protect is not the plumbing but the two properties that stop it becoming decoration:

* **A directive is measurable or it is not accepted.** "Keep reserves above 100" can be checked
  next turn; "play aggressively" cannot, and a mechanism that accepts the second accumulates
  ambition nobody can score.
* **A missing measurement never reads as a passing one.** This is the failure that would make the
  whole thing quietly worthless: a directive whose metric no adapter reports would look satisfied
  on every decision forever.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_amplifier.contract import Action, Directive, Orders, WorldView
from neural_amplifier.directives import (
    DirectiveError,
    DirectiveStore,
    accept,
    evaluate,
    tradeoffs,
    validate,
)


def view(
    *,
    turn: int = 35,
    metrics: dict[str, float] | None = None,
    actions: list[Action] | None = None,
) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=turn,
        faction="University",
        surface_id="base.hurry",
        action_space=actions or [],
        metrics=metrics,
    )


def saving(**overrides: object) -> Directive:
    """The user's motivating example: save energy for the secret project, priority 7."""
    base = {
        "id": "fund-secret-project",
        "intent": "Bank energy to rush the secret project the moment it is available.",
        "metric": "energy_reserves",
        "comparator": "at_least",
        "target": 300.0,
        "priority": 7,
        "horizon_turn": 60,
    }
    base.update(overrides)
    return Directive(**base)  # type: ignore[arg-type]


# --- measurability ---------------------------------------------------------


def test_a_directive_naming_an_unknown_metric_is_refused() -> None:
    """The enforcement point. Rejected at issue time, while the author is still in the loop."""
    with pytest.raises(DirectiveError, match="unknown metric"):
        validate(saving(metric="aggression"))


def test_an_absolute_comparator_without_a_target_is_refused() -> None:
    with pytest.raises(DirectiveError, match="nothing to compare"):
        validate(saving(target=None))


def test_a_rejected_directive_does_not_cost_the_decision_it_arrived_with() -> None:
    """The choice may be perfectly good even where the plan attached to it was not expressible."""
    good = saving()
    bad = saving(id="vibes", metric="aggression")
    accepted, rejected = accept([good, bad], view(metrics={"energy_reserves": 82}))

    assert [d.id for d in accepted] == ["fund-secret-project"]
    assert len(rejected) == 1
    assert "aggression" in rejected[0]


def test_an_unreported_metric_is_unmeasurable_not_satisfied() -> None:
    """The failure this whole design is arranged to prevent.

    A directive whose metric nobody reports must not look like one that is passing, or it steers
    nothing while appearing to work — for the rest of the game.
    """
    (status,) = evaluate([saving()], view(metrics={}))

    assert status.satisfied is None
    assert status.current is None
    assert "not reported" in (status.detail or "")


def test_a_relative_directive_is_refused_when_it_has_no_baseline() -> None:
    """``increase`` measured against nothing can never be evaluated.

    And unlike an absent metric, this one cannot be repaired later: nothing in a future turn can
    supply the value as of a turn already past.
    """
    _, rejected = accept(
        [saving(comparator="increase", target=None)], view(metrics={"base_count": 4})
    )
    assert len(rejected) == 1
    assert "at issue time" in rejected[0]


def test_the_baseline_is_stamped_from_the_world_view_not_the_model() -> None:
    accepted, _ = accept(
        [saving(comparator="increase", target=None)], view(metrics={"energy_reserves": 82})
    )
    assert accepted[0].baseline == 82
    assert accepted[0].issued_turn == 35


# --- evaluation ------------------------------------------------------------


def test_an_absolute_bound_is_checked_against_the_current_value() -> None:
    (unsatisfied,) = evaluate([saving()], view(metrics={"energy_reserves": 82}))
    (satisfied,) = evaluate([saving()], view(metrics={"energy_reserves": 310}))

    assert unsatisfied.satisfied is False
    assert "82" in (unsatisfied.detail or "") and "300" in (unsatisfied.detail or "")
    assert satisfied.satisfied is True


def test_a_relative_directive_is_measured_against_its_baseline() -> None:
    grew = evaluate(
        [saving(comparator="increase", target=None, baseline=82.0)],
        view(metrics={"energy_reserves": 120}),
    )
    shrank = evaluate(
        [saving(comparator="increase", target=None, baseline=82.0)],
        view(metrics={"energy_reserves": 40}),
    )

    assert grew[0].satisfied is True
    assert "+38" in (grew[0].detail or "")
    assert shrank[0].satisfied is False


def test_hold_tolerates_ordinary_movement() -> None:
    """A directive violated by noise gets ignored, by a model as surely as by a person."""
    held = evaluate(
        [saving(comparator="hold", target=None, baseline=100.0)],
        view(metrics={"energy_reserves": 105}),
    )
    broken = evaluate(
        [saving(comparator="hold", target=None, baseline=100.0)],
        view(metrics={"energy_reserves": 140}),
    )

    assert held[0].satisfied is True
    assert broken[0].satisfied is False


def test_an_expired_directive_is_not_shown_at_all() -> None:
    """Leaving a finished plan in front of a model as though it still applied is misleading."""
    assert evaluate([saving(horizon_turn=30)], view(turn=35, metrics={"energy_reserves": 82})) == []


# --- trade-offs ------------------------------------------------------------


def _hurry_actions() -> list[Action]:
    """``base.hurry`` as the adapter reports it: 81 credits to finish a Colony Pod early."""
    return [
        Action(id="hurry:none", action="Do not hurry"),
        Action(id="hurry:now", action="Hurry production", effects={"energy_reserves": -81.0}),
    ]


def test_a_tradeoff_quantifies_what_an_action_costs_the_plan() -> None:
    """The half that makes a priority number usable rather than an assertion."""
    world = view(metrics={"energy_reserves": 82, "energy_income": 14}, actions=_hurry_actions())
    (trade,) = tradeoffs([saving()], world)

    assert trade.action_id == "hurry:now"
    assert trade.directive_id == "fund-secret-project"
    assert trade.delta == -81
    assert trade.projected == 1
    assert trade.directive_priority == 7
    # 81 credits at +14/turn. The number a decision can actually weigh against "seven turns
    # earlier on a Colony Pod".
    assert trade.setback_turns == pytest.approx(5.8, abs=0.05)


def test_setback_is_omitted_rather_than_invented_when_no_rate_is_reported() -> None:
    """A setback figure with a made-up denominator is worse than none — it looks like the one
    piece of hard arithmetic in the block."""
    world = view(metrics={"energy_reserves": 82}, actions=_hurry_actions())
    (trade,) = tradeoffs([saving()], world)
    assert trade.setback_turns is None


def test_would_violate_flags_only_a_directive_that_is_being_met_now() -> None:
    """A directive already failing is not made worse by failing again, and one that stays
    satisfied needs no argument. Only the transition deserves the model's attention."""
    breaking = tradeoffs(
        [saving(target=50.0)],
        view(metrics={"energy_reserves": 82, "energy_income": 14}, actions=_hurry_actions()),
    )
    already_failing = tradeoffs(
        [saving(target=300.0)],
        view(metrics={"energy_reserves": 82, "energy_income": 14}, actions=_hurry_actions()),
    )

    assert breaking[0].would_violate is True
    assert already_failing[0].would_violate is False


def test_actions_that_do_not_touch_the_metric_produce_nothing() -> None:
    """Keeps the block empty on the many decisions where no plan is at stake, which is both
    cheaper and a clearer signal that something IS at stake when it is not empty."""
    world = view(
        metrics={"energy_reserves": 82},
        actions=[Action(id="unit:0", action="Scout Patrol", effects={"mineral_surplus": -1.0})],
    )
    assert tradeoffs([saving()], world) == []


def test_a_projection_needs_a_current_value() -> None:
    """Reporting a delta with no baseline invites reading the delta as the resulting level."""
    world = view(metrics={"energy_income": 14}, actions=_hurry_actions())
    assert tradeoffs([saving()], world) == []


# --- the store -------------------------------------------------------------


def test_reissuing_a_directive_replaces_it() -> None:
    """A long-horizon decision restates a plan it still believes in; two contradictory copies of
    one id would be worse than either."""
    store = DirectiveStore()
    store.add([saving(target=300.0)])
    store.add([saving(target=400.0)])

    (live,) = store.in_force(turn=40)
    assert len(store) == 1
    assert live.target == 400.0


def test_in_force_drops_what_has_expired() -> None:
    store = DirectiveStore()
    store.add([saving(horizon_turn=30), saving(id="grow", metric="base_count", horizon_turn=99)])

    assert [d.id for d in store.in_force(turn=35)] == ["grow"]
    assert len(store) == 1  # and the expired one is gone, not merely hidden


def test_directives_survive_a_restart(tmp_path: Path) -> None:
    """A plan that does not outlive the process is not a long-horizon plan."""
    path = tmp_path / "plan.json"
    DirectiveStore(path).add([saving()])

    reloaded = DirectiveStore(path)
    (live,) = reloaded.in_force(turn=40)
    assert live.id == "fund-secret-project"
    assert live.priority == 7


def test_a_corrupt_plan_file_costs_the_plan_not_the_game(tmp_path: Path) -> None:
    """Losing the plan is bad; refusing to play is worse. A game with no standing directives is
    where every game starts."""
    path = tmp_path / "plan.json"
    path.write_text("{ this is not json", encoding="utf-8")

    store = DirectiveStore(path)
    assert store.in_force(turn=35) == []


def test_the_persisted_file_is_readable_by_a_person(tmp_path: Path) -> None:
    """The plan is the most human-interesting state we keep; it should not need a tool to read."""
    path = tmp_path / "plan.json"
    DirectiveStore(path).add([saving()])

    payload = json.loads(path.read_text(encoding="utf-8"))
    (entry,) = payload["directives"]
    assert entry["intent"].startswith("Bank energy")
    assert entry["priority"] == 7


def test_ordering_is_the_order_the_plan_was_made() -> None:
    store = DirectiveStore()
    store.add([saving(id="second", issued_turn=20), saving(id="first", issued_turn=10)])
    assert [d.id for d in store.in_force(turn=30)] == ["first", "second"]


# --- the response side -----------------------------------------------------


def test_orders_can_carry_a_directive_and_report_what_it_followed() -> None:
    """The contract seam. Without these fields the long-horizon decision has no way to speak to
    the tactical one, and the tactical one no way to say whether it listened."""
    orders = Orders.model_validate(
        {
            "choices": [{"action_id": "hurry:none", "reason": "saving for the project"}],
            "directives": [saving().model_dump(mode="json")],
            "followed": ["fund-secret-project"],
            "overrode": [],
        }
    )
    assert orders.directives[0].priority == 7
    assert orders.followed == ["fund-secret-project"]
