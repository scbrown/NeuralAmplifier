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

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Action, Choice, Directive, Orders, WorldView
from neural_amplifier.directives import (
    DirectiveError,
    DirectiveStore,
    accept,
    evaluate,
    relevant,
    tradeoffs,
    validate,
)
from neural_amplifier.knowledge import Grounding
from neural_amplifier.metrics import VOCABULARY
from neural_amplifier.orchestrator import Orchestrator


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


# --- relevance: which directives a decision is even shown --------------------


def _many(count: int, **overrides: object) -> list[Directive]:
    """A plan large enough that showing all of it would be the wrong answer."""
    return [
        saving(id=f"d{i}", metric="base_count", target=float(i), **overrides) for i in range(count)
    ]


def test_a_decision_is_shown_the_directive_whose_resource_it_is_about_to_spend() -> None:
    """The case the whole mechanism exists for.

    A game accumulates hundreds of directives, so this one has to *win* against noise rather than
    merely be present — an action's effects naming the directive's metric is the strongest signal
    there is.
    """
    world = view(metrics={"energy_reserves": 82, "base_count": 4}, actions=_hurry_actions())
    selection = relevant([*_many(20), saving()], world, limit=3)

    assert selection.selected[0].id == "fund-secret-project"
    assert len(selection.selected) == 3


def test_a_decision_is_shown_the_directive_naming_the_entity_it_concerns() -> None:
    """The link the user asked for: pull the plan from the project it is about."""
    linked = saving(id="build-transit", entities=["fac:the-planetary-transit-system"])
    world = view(metrics={"base_count": 4}, actions=[Action(id="a0", action="x")])

    selection = relevant(
        [*_many(20), linked], world, entity_ids=["fac:the-planetary-transit-system"], limit=2
    )

    assert "build-transit" in [d.id for d in selection.selected]


def test_an_unrelated_entity_does_not_pull_the_directive() -> None:
    linked = saving(id="build-transit", entities=["fac:the-planetary-transit-system"])
    world = view(metrics={"base_count": 4})

    selection = relevant(
        [*_many(20), linked], world, entity_ids=["fac:the-weather-paradigm"], limit=2
    )

    assert "build-transit" not in [d.id for d in selection.selected]


def test_what_is_cut_is_reported_rather_than_silently_dropped() -> None:
    """A silent cap is what makes a plan look served when it was never read. With hundreds of
    directives, "not mentioned" and "never offered" are different problems."""
    selection = relevant(_many(12), view(metrics={"base_count": 4}), limit=5)

    assert len(selection.selected) == 5
    assert len(selection.dropped) == 7
    assert set(selection.dropped).isdisjoint({d.id for d in selection.selected})


def test_survival_priority_is_shown_even_when_unrelated() -> None:
    """A plan nobody is told about cannot be followed, and at priority 9-10 that is the game."""
    critical = saving(
        id="dont-lose-hq", metric="drone_total", comparator="at_most", target=2.0, priority=10
    )
    selection = relevant([*_many(20), critical], view(metrics={"base_count": 4}), limit=3)

    assert "dont-lose-hq" in [d.id for d in selection.selected]


def test_priority_only_breaks_ties_and_does_not_override_relevance() -> None:
    """A priority-8 directive about something this decision cannot affect should lose to a
    priority-3 one about the resource being spent — otherwise the plan drowns the decision."""
    loud = saving(id="loud", metric="base_count", priority=8)
    apt = saving(id="apt", metric="energy_reserves", priority=3)
    world = view(metrics={"energy_reserves": 82, "base_count": 4}, actions=_hurry_actions())

    selection = relevant([loud, apt], world, limit=1)

    assert [d.id for d in selection.selected] == ["apt"]


def test_expired_directives_are_never_selected() -> None:
    selection = relevant([saving(horizon_turn=30)], view(turn=35, metrics={"energy_reserves": 82}))
    assert selection.selected == []


def test_the_store_finds_directives_by_the_entity_they_name() -> None:
    """The reverse index: given the project a base is considering, find the plans about it."""
    store = DirectiveStore()
    store.add(
        [
            saving(id="transit", entities=["fac:the-planetary-transit-system"]),
            saving(id="weather", entities=["fac:the-weather-paradigm"]),
        ]
    )

    found = store.for_entities(["fac:the-weather-paradigm"])
    assert [d.id for d in found] == ["weather"]
    assert store.for_entities([]) == []


def test_every_directive_has_at_least_one_graph_pointer() -> None:
    """The standing rule that a node without a datalinks pointer is a defect. It holds here by
    construction — ``metric`` is mandatory and names the resource — so ``entities`` is the
    optional half that ties a directive to the specific thing it is for."""
    bare = saving()
    assert bare.entities == []
    assert bare.metric in VOCABULARY


# --- multi-hop: from the resource spent to the strategy it serves ------------


def test_spending_a_resource_reaches_the_strategy_behind_it() -> None:
    """The chain the mechanism is for, end to end.

    Hurrying spends energy credits; that pulls the directive saving them; that directive names a
    secret project; and the project is named by a higher-order plan. None of the last three are
    connected to this base's decision by anything a single-hop lookup could see, yet they are
    exactly what makes 81 credits expensive.
    """
    saving_for = saving(
        id="fund-weather-paradigm",
        metric="energy_reserves",
        entities=["fac:the-weather-paradigm"],
    )
    strategy = saving(
        id="terraform-victory",
        metric="base_count",
        comparator="increase",
        target=None,
        baseline=4.0,
        priority=8,
        entities=["fac:the-weather-paradigm"],
    )
    noise = _many(30)

    world = view(metrics={"energy_reserves": 82, "base_count": 4}, actions=_hurry_actions())
    selection = relevant([*noise, saving_for, strategy], world, limit=4)

    reached = {h.directive.id: h for h in selection.hits}
    assert "fund-weather-paradigm" in reached
    assert "terraform-victory" in reached

    # And the path is legible, because the path is the argument.
    assert reached["fund-weather-paradigm"].hop == 0
    assert "energy_reserves" in reached["fund-weather-paradigm"].via
    assert reached["terraform-victory"].hop == 1
    assert reached["terraform-victory"].via == "fund-weather-paradigm → fac:the-weather-paradigm"


def test_the_walk_does_not_expand_through_shared_metrics() -> None:
    """Half a plan hangs off ``base_count``. Following metrics transitively would turn a targeted
    walk into a broadcast with extra steps."""
    hub = saving(id="hub", metric="energy_reserves", entities=["fac:the-weather-paradigm"])
    # Shares a metric with the hop-1 directive but names no shared entity.
    stranger = saving(id="stranger", metric="base_count", entities=["fac:the-command-nexus"])
    linked = saving(id="linked", metric="base_count", entities=["fac:the-weather-paradigm"])

    world = view(metrics={"energy_reserves": 82}, actions=_hurry_actions())
    selection = relevant([hub, linked, stranger], world, limit=2)

    ids = [h.directive.id for h in selection.hits]
    assert ids == ["hub", "linked"]
    assert "stranger" in selection.dropped


def test_the_shortest_path_to_a_directive_is_the_one_reported() -> None:
    """A directive reachable both directly and round the houses should read as directly
    relevant — the shortest route is both the strongest and the clearest."""
    both = saving(id="both", metric="energy_reserves", entities=["fac:the-weather-paradigm"])
    other = saving(id="other", metric="energy_reserves", entities=["fac:the-weather-paradigm"])

    world = view(metrics={"energy_reserves": 82}, actions=_hurry_actions())
    selection = relevant([both, other], world)

    assert {h.hop for h in selection.hits} == {0}


def test_the_hop_path_travels_onto_the_status_the_model_sees() -> None:
    """Provenance that stops at the selector is provenance the decision never gets."""
    saving_for = saving(id="fund", metric="energy_reserves", entities=["fac:the-weather-paradigm"])
    strategy = saving(id="strat", metric="base_count", entities=["fac:the-weather-paradigm"])
    world = view(metrics={"energy_reserves": 82, "base_count": 4}, actions=_hurry_actions())

    statuses = evaluate(relevant([saving_for, strategy], world).hits, world)

    by_id = {s.directive.id: s for s in statuses}
    assert by_id["fund"].hop == 0
    assert by_id["strat"].via == "fund → fac:the-weather-paradigm"


def test_hops_can_be_bounded() -> None:
    """Beyond a couple of steps the connection is too weak to be worth prompt space."""
    a = saving(id="a", metric="energy_reserves", entities=["e1"])
    b = saving(id="b", metric="base_count", entities=["e1", "e2"])
    c = saving(id="c", metric="pop_total", entities=["e2"])
    world = view(metrics={"energy_reserves": 82}, actions=_hurry_actions())

    one = relevant([a, b, c], world, hops=1, limit=3)
    two = relevant([a, b, c], world, hops=2, limit=3)

    assert {h.directive.id: h.hop for h in one.hits if h.hop <= 1}.keys() >= {"a", "b"}
    assert [h.hop for h in two.hits if h.directive.id == "c"] == [2]


def test_unrelated_directives_are_not_used_as_padding() -> None:
    """Room in the block is not a reason to fill it.

    Measured before this rule: with 16 directives in the plan, half of a four-slot block went to
    entries whose own explanation read "not related to this decision". That costs prompt space on
    every decision and teaches the model that most of the block is noise, which is the fastest way
    to have it stop reading the two entries that matter.
    """
    relevant_one = saving(id="fund", metric="energy_reserves")
    world = view(metrics={"energy_reserves": 82}, actions=_hurry_actions())

    selection = relevant([relevant_one, *_many(10)], world, limit=8)

    assert [h.directive.id for h in selection.hits] == ["fund"]
    assert len(selection.dropped) == 10


def test_a_measurable_directive_is_still_worth_a_slot() -> None:
    """It can at least be checked here, which is how a slow drift gets noticed."""
    drifting = saving(id="grow", metric="base_count", target=9.0)
    world = view(metrics={"energy_reserves": 82, "base_count": 4}, actions=_hurry_actions())

    assert "grow" in [h.directive.id for h in relevant([drifting], world).hits]


def test_survival_intent_is_shown_even_though_unrelated() -> None:
    critical = saving(
        id="hold-hq", metric="drone_total", comparator="at_most", target=2.0, priority=10
    )
    world = view(metrics={"energy_reserves": 82}, actions=_hurry_actions())

    assert "hold-hq" in [h.directive.id for h in relevant([critical, *_many(10)], world).hits]


# --- the plan's own id space, end to end (na-zgz) ---------------------------


class _Retriever:
    """Facts with ids, which is what makes a citation checkable at all."""

    def retrieve(self, world_view: WorldView) -> Grounding:
        return Grounding(
            facts=("Weather Paradigm doubles terraforming rate.", "Formers terraform terrain."),
            fact_ids=("fac:weather-paradigm-rule", "unit:formers"),
        )


def _citing_brain() -> ScriptedBrain:
    """Cites one retrieved fact and one entity only the directive named."""
    return ScriptedBrain(
        [
            Orders(
                choices=[Choice(action_id="hurry:none", reason="saving for the project")],
                cited=["unit:formers", "fac:the-weather-paradigm"],
            )
        ]
    )


def _plan(tmp_path: Path) -> DirectiveStore:
    store = DirectiveStore(tmp_path / "plan.json")
    store.add([saving(id="fund", entities=["fac:the-weather-paradigm"])])
    return store


def _world() -> WorldView:
    return view(metrics={"energy_reserves": 82}, actions=_hurry_actions())


def test_a_citation_the_plan_alone_offered_is_recorded_not_discarded(tmp_path: Path) -> None:
    """The other half of na-zgz. ``summarise`` filters citations against grounding — correctly,
    or utilisation would stop being about retrieval — so before this the id was dropped there
    *and* flagged as fabricated by the guard: a decision reasoning from an entity the plan put
    in front of it was indistinguishable from one that read nothing."""
    result = Orchestrator(_citing_brain(), retriever=_Retriever(), plan=_plan(tmp_path)).decide(
        _world()
    )

    assert result.record.plan.entities_cited == ["fac:the-weather-paradigm"]
    # Still absent from the retrieval half — it was never retrieved.
    assert result.record.knowledge.quipu_cited == ["unit:formers"]


def test_directives_do_not_move_grounding_utilisation(tmp_path: Path) -> None:
    """Acceptance criterion for na-zgz. Utilisation answers "was retrieval read?"; folding the
    plan's entities into either half would make a retrieval metric drift every time the plan
    changed shape, and push it above 1.0 the moment a decision cited one."""
    without = Orchestrator(_citing_brain(), retriever=_Retriever()).decide(_world())
    with_plan = Orchestrator(_citing_brain(), retriever=_Retriever(), plan=_plan(tmp_path)).decide(
        _world()
    )

    assert with_plan.record.plan.in_force == ["fund"], "the plan must actually be in play"
    assert without.record.knowledge.utilisation == with_plan.record.knowledge.utilisation == 0.5
    assert without.record.plan.entities_cited == []


def test_an_entity_only_grounding_offered_is_not_claimed_by_the_plan(tmp_path: Path) -> None:
    """Grounding wins where both showed the id: there it is already counted as retrieval doing
    its job, and counting it twice would make the plan look like it contributed retrieval."""
    store = DirectiveStore(tmp_path / "plan.json")
    store.add([saving(id="fund", entities=["unit:formers"])])
    result = Orchestrator(_citing_brain(), retriever=_Retriever(), plan=store).decide(_world())

    assert result.record.plan.entities_cited == []
    assert result.record.knowledge.quipu_cited == ["unit:formers"]
