"""Checking an order against the state it is about to be applied to.

The guard the agent-brain pivot made necessary. A model answered in seconds with the game
blocked, so the board could not move between the snapshot and the apply. An agent takes as long
as it likes and keeps a session across a whole game, so it can reason from a belief about a base
rather than from the world view in front of it — and the adapter replays one cached decision
across the engine's several calls per base-turn, by which point the board genuinely has moved.

What is testable is bounded by what is buildable: with no hot board graph (Hank role (d), not
built) this reasons only over numbers the world view already declares. Every check is arithmetic
on figures the adapter published, which is why a denial here is a fact rather than a guess.
"""

from __future__ import annotations

from neural_amplifier.contract import Action, Choice, Orders, Tradeoff, WorldView
from neural_amplifier.hank import CitationGuard, GuardChain, StateGuard
from neural_amplifier.knowledge import Ruling, apply


def view(
    actions: list[Action],
    metrics: dict[str, float] | None = None,
    tradeoffs: list[Tradeoff] | None = None,
) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=42,
        faction="Gaians",
        surface_id="base.hurry",
        action_space=actions,
        metrics=metrics,
        tradeoffs=tradeoffs,
    )


HURRY = Action(
    id="hurry:now",
    action="Hurry production",
    effects={"energy_reserves": -81.0, "minerals_remaining": -26.0},
)
WAIT = Action(id="hurry:none", action="Do not hurry")


def test_an_affordable_order_is_allowed() -> None:
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 82})
    ruling = StateGuard().rule(Orders(choices=[Choice(action_id="hurry:now")]), world_view)
    assert ruling.verdict == "allow"
    assert ruling.stripped == ()


def test_an_order_the_state_can_no_longer_pay_for_is_denied() -> None:
    """The case this exists for.

    The action space offered `hurry:now` when reserves were 82. By the time the order lands
    they are 40 — the agent spent them elsewhere, or reasoned from a stale memory of the board.
    Still legal by the engine's list; no longer payable.
    """
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 40})
    orders = Orders(choices=[Choice(action_id="hurry:now")])
    ruling = StateGuard().rule(orders, world_view)
    assert ruling.verdict == "deny"
    assert ruling.stripped == ("hurry:now",)
    assert "only 40" in ruling.advisories[0]
    # And the denial actually removes it, rather than merely commenting on it.
    assert apply(orders, ruling).choices == []


def test_spending_exactly_what_is_available_is_allowed() -> None:
    """Reserves to zero is a legitimate move, and an off-by-one here would forbid every
    all-in purchase the game permits."""
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 81})
    assert (
        StateGuard().rule(Orders(choices=[Choice(action_id="hurry:now")]), world_view).verdict
        == "allow"
    )


def test_an_unreported_metric_is_uncheckable_not_violated() -> None:
    """Absent must never read as failing.

    The alternative is inventing a baseline and denying a legal move on the strength of it,
    which turns a gap in the adapter into a wrong answer in the game.
    """
    world_view = view([HURRY, WAIT], metrics={"labs_output": 6})
    assert (
        StateGuard().rule(Orders(choices=[Choice(action_id="hurry:now")]), world_view).verdict
        == "allow"
    )


def test_no_metrics_at_all_allows() -> None:
    assert (
        StateGuard()
        .rule(Orders(choices=[Choice(action_id="hurry:now")]), view([HURRY, WAIT]))
        .verdict
        == "allow"
    )


def test_an_action_with_no_declared_effects_is_not_second_guessed() -> None:
    """`effects` is a declaration, and its absence is not a claim of zero cost. Guessing a
    cost from elsewhere in the payload is exactly the engine knowledge the orchestrator must
    not hold (invariant 2)."""
    world_view = view([WAIT], metrics={"energy_reserves": 0})
    assert (
        StateGuard().rule(Orders(choices=[Choice(action_id="hurry:none")]), world_view).verdict
        == "allow"
    )


def test_gains_are_never_denied() -> None:
    """A positive effect cannot make a metric unaffordable, and treating it symmetrically
    would deny orders that improve the position."""
    gain = Action(id="sell:x", action="Sell", effects={"energy_reserves": +50})
    world_view = view([gain], metrics={"energy_reserves": 0})
    assert (
        StateGuard().rule(Orders(choices=[Choice(action_id="sell:x")]), world_view).verdict
        == "allow"
    )


# ------------------------------------------------- a shortfall is not a budget (na-co2)


#: The live action space, turn 42, Morgan Solutions — Colony Pod already in production with 27
#: of its 33 minerals banked and one turn to go, so the reported shortfall is 6. The three
#: cheapest of the nine options the base actually had.
#:
#: The costs are here and the effects are not, which is the fix as the adapter now emits it: a
#: build order spends nothing on the turn it is given, so there is no immediate delta to declare
#: (thinker `na_write_action_space`).
CONTINUE = Action(
    id="unit:0", action="Colony Pod", cost=33, turns_if_switched=5, turns_if_continued=1
)
SWITCH_FORMERS = Action(id="unit:1", action="Formers", cost=22, turns_if_switched=4)
SWITCH_SCOUT = Action(id="unit:2", action="Scout Patrol", cost=11, turns_if_switched=2)
MID_BUILD = {"minerals_remaining": 6, "mineral_surplus": 7, "pop_size": 4}


def test_continuing_the_item_already_in_production_is_applied() -> None:
    """na-co2's acceptance criterion, at the shape it was measured.

    The agent chose the item the base was one turn from finishing. The guard denied it — and
    with it every other option — because it read `minerals_remaining` 6 as a budget and the
    item's 33-mineral cost as a withdrawal from it. `base.production` therefore had no
    llm-tier decision to its name while `faction.se` had four: the surface was not failing
    visibly, it was silently degrading to the deterministic tier on every developed base.
    """
    orders = Orders(choices=[Choice(action_id="unit:0")])
    ruling = StateGuard().rule(orders, view([CONTINUE, SWITCH_FORMERS, SWITCH_SCOUT], MID_BUILD))

    assert ruling.verdict == "allow"
    assert ruling.stripped == ()
    assert apply(orders, ruling).choices[0].action_id == "unit:0"


def test_the_whole_decision_lands_rather_than_degrading() -> None:
    """The measured symptom was not "an advisory fired" — it was `degraded=true`.

    Every option in the space cost more than the shortfall, so the guard stripped all of them,
    the repair ask could only be answered with another denied option, and the decision fell to
    the deterministic tier. The game still got a legal item, which is why this was invisible as
    a quality problem: the run just quietly stopped being an llm-tier run. Asserting at the
    orchestrator is what distinguishes "the guard allows" from "the decision survives".
    """
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.orchestrator import Orchestrator

    brain = ScriptedBrain(chooser=lambda _: Orders(choices=[Choice(action_id="unit:0")]))
    result = Orchestrator(brain=brain, guard=StateGuard(), repair_attempts=1).decide(
        view([CONTINUE, SWITCH_FORMERS, SWITCH_SCOUT], MID_BUILD)
    )

    assert result.record.degraded is False
    assert result.record.degrade_reason is None
    assert result.orders.choices[0].action_id == "unit:0"
    assert len(brain.calls) == 1, "an allowed decision must not be re-asked"


def test_no_advisory_claims_the_state_moved() -> None:
    """The other half of the acceptance criterion, and worth its own test.

    Nothing raced here. The advisory used to end "the state moved since this was offered",
    asserting a cause the guard cannot observe — it compares a declared effect against a
    reported metric and cannot tell a moved board from a stale belief from an adapter
    declaring an effect it did not mean. It was always the third. A wrong cause on the record
    costs a reader an afternoon hunting a concurrency bug that does not exist.
    """
    ruling = StateGuard().rule(
        Orders(choices=[Choice(action_id="unit:0")]),
        view([CONTINUE, SWITCH_FORMERS, SWITCH_SCOUT], MID_BUILD),
    )
    assert ruling.advisories == ()

    # And not merely because this ruling is silent — the phrase is gone from the denial that
    # does fire, which is where it used to appear.
    denied = StateGuard().rule(
        Orders(choices=[Choice(action_id="hurry:now")]), view([HURRY], {"energy_reserves": 40})
    )
    assert denied.verdict == "deny"
    assert not any("state moved" in a for a in denied.advisories)
    assert "only 40 is available" in denied.advisories[0]


def test_a_shortfall_declared_as_an_effect_is_never_an_affordability_failure() -> None:
    """The general fix (na-co2 (b)), tested at the guard rather than at the adapter.

    Fixing only the adapter would leave the trap armed for the next metric that counts
    downwards. `minerals_remaining` is "lower is better" and a negative delta against it is an
    *improvement* — a debt shrinking — so it can never make an order unaffordable however large
    it is. Here it is larger than the reported value, which is the exact arithmetic that used to
    deny.
    """
    liar = Action(id="unit:0", action="Colony Pod", effects={"minerals_remaining": -33})
    ruling = StateGuard().rule(
        Orders(choices=[Choice(action_id="unit:0")]), view([liar], MID_BUILD)
    )
    assert ruling.verdict == "allow"
    assert ruling.advisories == ()


def test_a_declared_pool_is_still_checked() -> None:
    """Narrowing the check must not disarm it.

    `energy_reserves` is the one metric the vocabulary declares a pool, and base.hurry is the
    surface that spends it. If this passes and the test above also passes, the guard is
    discriminating on the declaration rather than having simply stopped working.
    """
    both = Action(
        id="hurry:now",
        action="Hurry production",
        effects={"energy_reserves": -81, "minerals_remaining": -26},
    )
    ruling = StateGuard().rule(
        Orders(choices=[Choice(action_id="hurry:now")]),
        view([both], {"energy_reserves": 40, "minerals_remaining": 26}),
    )
    assert ruling.verdict == "deny"
    assert ruling.stripped == ("hurry:now",)
    # One advisory, not two: the shortfall leg contributed nothing.
    assert len(ruling.advisories) == 1
    assert "energy_reserves" in ruling.advisories[0]


def test_an_effect_on_a_name_outside_the_vocabulary_is_not_a_budget() -> None:
    """`faction.se` declares its deltas under the engine's own effect names — "economy",
    "efficiency", "support" — which are not measurements at all. An unknown name has no
    declaration to read, so it cannot be a pool, and a negative one must not deny."""
    se = Action(id="se:1:2", action="Planned", effects={"economy": -1, "growth": +2})
    ruling = StateGuard().rule(
        Orders(choices=[Choice(action_id="se:1:2")]), view([se], {"economy": 0})
    )
    assert ruling.verdict == "allow"


def test_a_directive_on_a_shortfall_is_still_weighed() -> None:
    """Not checking affordability is not the same as ignoring the metric.

    The trade-off path is untouched: a directive about `minerals_remaining` still lands as an
    advisory when the chosen action would violate it. Denial and weighing are separate
    mechanisms, and only the first one was wrong.
    """
    tradeoff = Tradeoff(
        action_id="unit:0",
        directive_id="finish-what-we-start",
        metric="minerals_remaining",
        delta=27,
        projected=33,
        would_violate=True,
        directive_priority=6,
    )
    ruling = StateGuard().rule(
        Orders(choices=[Choice(action_id="unit:0")]),
        view([CONTINUE], MID_BUILD, tradeoffs=[tradeoff]),
    )
    assert ruling.verdict == "allow"
    assert any("finish-what-we-start" in a for a in ruling.advisories)


def test_a_violated_directive_warns_but_never_denies() -> None:
    """Priorities exist so a decision can *outrank* a plan.

    Denying here would make directives absolute, which the design explicitly rejects — a
    standing plan losing to an urgent move is the mechanism working, not failing.
    """
    tradeoff = Tradeoff(
        action_id="hurry:now",
        directive_id="fund-weather-paradigm",
        metric="energy_reserves",
        delta=-81,
        projected=1,
        would_violate=True,
        directive_priority=7,
    )
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 82}, tradeoffs=[tradeoff])
    orders = Orders(choices=[Choice(action_id="hurry:now")])
    ruling = StateGuard().rule(orders, world_view)
    assert ruling.verdict == "allow"
    assert ruling.stripped == ()
    assert any("fund-weather-paradigm" in a and "priority 7" in a for a in ruling.advisories)
    assert apply(orders, ruling).choices, "a warning must not remove the choice"


def test_a_directive_on_an_unchosen_action_is_silent() -> None:
    """Only what was actually chosen is worth an advisory; the rest is noise on the record."""
    tradeoff = Tradeoff(
        action_id="hurry:now",
        directive_id="fund-weather-paradigm",
        metric="energy_reserves",
        delta=-81,
        projected=1,
        would_violate=True,
    )
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 82}, tradeoffs=[tradeoff])
    ruling = StateGuard().rule(Orders(choices=[Choice(action_id="hurry:none")]), world_view)
    assert ruling.advisories == ()


def test_an_action_outside_the_space_is_left_to_validate() -> None:
    """validate() strips these before the guard sees them. Reaching here means the two
    disagree, which is not this guard's bug to fix — nor its bug to hide."""
    world_view = view([WAIT], metrics={"energy_reserves": 0})
    assert (
        StateGuard().rule(Orders(choices=[Choice(action_id="unit:999")]), world_view).verdict
        == "allow"
    )


# ------------------------------------------------------------------------ chain


class Denies:
    name = "denies"

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        return Ruling(verdict="deny", stripped=("hurry:now",), reason="nope")


class Allows:
    name = "allows"

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        return Ruling(verdict="allow", advisories=("fine by me",))


class Degraded:
    name = "degraded"

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        return Ruling(verdict="allow", degraded=True, reason="guard was down")


def test_deny_wins_regardless_of_order() -> None:
    """Otherwise registration order silently decides policy — a configuration bug that stays
    invisible until the day it matters."""
    orders = Orders(choices=[Choice(action_id="hurry:now")])
    world_view = view([HURRY])
    for chain in (GuardChain(Allows(), Denies()), GuardChain(Denies(), Allows())):
        ruling = chain.rule(orders, world_view)
        assert ruling.verdict == "deny"
        assert ruling.stripped == ("hurry:now",)


def test_the_chain_keeps_every_advisory() -> None:
    ruling = GuardChain(Allows(), StateGuard()).rule(
        Orders(choices=[Choice(action_id="hurry:now")]),
        view([HURRY], metrics={"energy_reserves": 10}),
    )
    assert "fine by me" in ruling.advisories
    assert any("only 10" in a for a in ruling.advisories)


def test_degradation_propagates_through_the_chain() -> None:
    """A record reporting a clean pass from a guard that never ran is worse than one
    reporting nothing."""
    ruling = GuardChain(StateGuard(), Degraded()).rule(
        Orders(choices=[Choice(action_id="hurry:none")]), view([WAIT])
    )
    assert ruling.degraded is True
    assert ruling.reason and "down" in ruling.reason


def test_an_empty_chain_allows() -> None:
    """A guard that is down allows, and so does one that was never wired."""
    assert GuardChain().rule(Orders(), view([WAIT])).verdict == "allow"


def test_the_chain_composes_the_two_real_guards() -> None:
    """The shipped configuration: state preconditions plus citation integrity."""
    chain = GuardChain(StateGuard(), CitationGuard())
    ruling = chain.rule(
        Orders(choices=[Choice(action_id="hurry:now")], cited=["fac:invented"]),
        view([HURRY], metrics={"energy_reserves": 5}),
    )
    assert ruling.verdict == "deny"
    assert any("only 5" in a for a in ruling.advisories)


# ------------------------------------------------------------------- repair loop


def test_a_denied_decision_is_re_asked_with_the_reason(tmp_path) -> None:
    """na-7zl: strip-then-degrade gives up a turn the brain could have salvaged.

    This mattered little while the only guard was CitationGuard, which never denies. StateGuard
    does — so a legal-but-unaffordable order now throws away a whole decision that one sentence
    of feedback would fix.
    """
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.orchestrator import Orchestrator

    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 40})
    # First the unaffordable choice, then a correction — what a brain given the reason would do.
    brain = ScriptedBrain(
        responses=[
            Orders(choices=[Choice(action_id="hurry:now")]),
            Orders(choices=[Choice(action_id="hurry:none")]),
        ]
    )
    result = Orchestrator(brain=brain, guard=StateGuard(), repair_attempts=1).decide(world_view)

    assert result.orders.choices[0].action_id == "hurry:none"
    assert result.record.degraded is False, "a repaired decision is not a degraded one"
    assert len(brain.calls) == 2, "the brain must be asked again"

    # And the second ask carried the reason, or the brain had nothing to repair from.
    second = brain.calls[1]
    assert second.advisories
    assert any("only 40" in a for a in second.advisories)


def test_the_repair_is_one_decision_not_two(tmp_path) -> None:
    """Exactly one record per decision, however many attempts it took.

    Two records would double count the surface, and coverage would read high while the game
    saw one build.
    """
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.decisions import DecisionLog
    from neural_amplifier.orchestrator import Orchestrator

    log_path = tmp_path / "decisions.jsonl"
    brain = ScriptedBrain(
        responses=[
            Orders(choices=[Choice(action_id="hurry:now")]),
            Orders(choices=[Choice(action_id="hurry:none")]),
        ]
    )
    Orchestrator(
        brain=brain, guard=StateGuard(), log=DecisionLog(log_path), repair_attempts=1
    ).decide(view([HURRY, WAIT], metrics={"energy_reserves": 40}))

    lines = [line for line in log_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1


def test_repair_is_bounded_and_then_degrades(tmp_path) -> None:
    """An unbounded repair loop is a game that never takes a turn."""
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.orchestrator import Orchestrator

    stubborn = ScriptedBrain(chooser=lambda _: Orders(choices=[Choice(action_id="hurry:now")]))
    result = Orchestrator(brain=stubborn, guard=StateGuard(), repair_attempts=1).decide(
        view([HURRY, WAIT], metrics={"energy_reserves": 40})
    )

    assert len(stubborn.calls) == 2, "one ask plus one repair, and no more"
    assert result.record.degraded is True
    assert "repair attempt" in (result.record.degrade_reason or "")
    # The safe fallback still ran — a failed repair must not cost the turn as well.
    assert result.orders.choices


def test_repairs_can_be_switched_off() -> None:
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.orchestrator import Orchestrator

    brain = ScriptedBrain(chooser=lambda _: Orders(choices=[Choice(action_id="hurry:now")]))
    Orchestrator(brain=brain, guard=StateGuard(), repair_attempts=0).decide(
        view([HURRY, WAIT], metrics={"energy_reserves": 40})
    )
    assert len(brain.calls) == 1


def test_an_allowed_decision_is_never_re_asked() -> None:
    """The loop must cost nothing on the overwhelming majority of decisions."""
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.orchestrator import Orchestrator

    brain = ScriptedBrain(chooser=lambda _: Orders(choices=[Choice(action_id="hurry:none")]))
    Orchestrator(brain=brain, guard=StateGuard(), repair_attempts=2).decide(
        view([HURRY, WAIT], metrics={"energy_reserves": 40})
    )
    assert len(brain.calls) == 1


def test_a_brain_error_is_not_repaired() -> None:
    """Repair is for a brain that answered badly. One that failed will fail again, and asking
    twice just doubles the delay before the fallback the game is waiting for."""
    from neural_amplifier.brain import BrainError, ScriptedBrain
    from neural_amplifier.orchestrator import Orchestrator

    broken = ScriptedBrain(raises=BrainError("no answer within 30s"))
    result = Orchestrator(brain=broken, guard=StateGuard(), repair_attempts=2).decide(
        view([HURRY, WAIT], metrics={"energy_reserves": 40})
    )
    assert len(broken.calls) == 1
    assert result.record.degraded is True


# --------------------------------------------------- citing a directive's entity


def _view_with_plan(cited: list[str], grounding: list[str] | None = None) -> WorldView:
    """A world view whose only fact ids come from a standing plan, not from retrieval."""
    from neural_amplifier.contract import Directive, DirectiveStatus

    return WorldView(
        engine="thinker",
        scope="base",
        turn=42,
        faction="Gaians",
        surface_id="base.hurry",
        action_space=[WAIT],
        grounding=grounding,
        directives=[
            DirectiveStatus(
                directive=Directive(
                    id="fund-weather-paradigm",
                    intent="save energy for the Weather Paradigm",
                    metric="energy_reserves",
                    comparator="at_least",
                    target=300,
                    entities=["fac:the-weather-paradigm"],
                ),
                current=82,
                satisfied=False,
            )
        ],
    )


def test_citing_a_directives_entity_is_not_a_fabrication() -> None:
    """na-zgz. The model was genuinely shown this id — inside the plan it was reasoning about.

    Measured before the fix: this fired on three to four runs in five, always on the entity of
    a directive the model had just been handed and had correctly used. The guard read the
    offered set from the grounding block only, so an honest citation looked invented.
    """
    orders = Orders(choices=[Choice(action_id="hurry:none")], cited=["fac:the-weather-paradigm"])
    ruling = CitationGuard().rule(orders, _view_with_plan([]))
    assert not any("never offered" in a for a in ruling.advisories)
    assert ruling.verdict == "allow"


def test_a_genuinely_invented_id_is_still_caught() -> None:
    """Widening "offered" must not disarm the check it exists for."""
    orders = Orders(choices=[Choice(action_id="hurry:none")], cited=["fac:invented"])
    ruling = CitationGuard().rule(orders, _view_with_plan([]))
    assert any("never offered" in a and "fac:invented" in a for a in ruling.advisories)


def test_a_plan_entity_citation_lands_on_the_record_separately() -> None:
    """The other half of the bug: fixing only the guard would still lose the citation.

    `quipu_cited` drives utilisation and must stay retrieval-only, or the number answers a
    different question. But a citation the model was legitimately shown cannot simply vanish —
    it would be invisible in both directions, neither flagged nor counted.
    """
    from neural_amplifier.directives import entities_shown
    from neural_amplifier.knowledge import Grounding, summarise
    from neural_amplifier.knowledge import Ruling as _Ruling

    world_view = _view_with_plan([], grounding=["fac:recycling-tanks increases minerals"])
    knowledge = summarise(
        Grounding(
            facts=("fac:recycling-tanks increases minerals",),
            fact_ids=("fac:recycling-tanks",),
        ),
        _Ruling(),
        guarded=True,
        cited=["fac:recycling-tanks", "fac:the-weather-paradigm"],
        shown_by_plan=entities_shown(world_view),
    )
    assert knowledge.quipu_cited == ["fac:recycling-tanks"]
    assert knowledge.plan_cited == ["fac:the-weather-paradigm"]
    # Utilisation still measures retrieval against retrieval — one fact offered, one used.
    assert knowledge.utilisation == 1.0


def test_an_invented_citation_reaches_neither_bucket() -> None:
    """A hallucinated id must not be laundered into the provenance block by either route."""
    from neural_amplifier.knowledge import Grounding, summarise
    from neural_amplifier.knowledge import Ruling as _Ruling

    knowledge = summarise(
        Grounding(facts=("fac:recycling-tanks x",), fact_ids=("fac:recycling-tanks",)),
        _Ruling(),
        guarded=True,
        cited=["fac:invented"],
        shown_by_plan={"fac:the-weather-paradigm"},
    )
    assert knowledge.quipu_cited == []
    assert knowledge.plan_cited == []


def test_a_directive_with_no_entities_changes_nothing() -> None:
    from neural_amplifier.contract import Directive, DirectiveStatus
    from neural_amplifier.directives import entities_shown

    world_view = WorldView(
        engine="thinker",
        scope="turn",
        turn=1,
        faction="Gaians",
        directives=[
            DirectiveStatus(
                directive=Directive(
                    id="d", intent="i", metric="energy_reserves", comparator="increase"
                )
            )
        ],
    )
    assert entities_shown(world_view) == set()
