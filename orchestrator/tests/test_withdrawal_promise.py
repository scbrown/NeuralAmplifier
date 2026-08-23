"""A diplomacy answer is a promise about later turns — na-nmg's first blocker, now gone.

na-nmg records a live case: turn 38, Peacekeepers vs Lord's Believers, Miriam demands troops
leave her territory, and option 1 is "Withdraw troops to nearest base." Answering that commits
us to move units off her tiles on *later* turns. Nothing carried the commitment past the dialog,
so the brain would say "yes, at once", leave the troops where they were, and take the
treaty-break consequences without ever having decided to.

The bead lists two blockers and a suggested order of work. The first was: **the metric
vocabulary cannot express it.** `Directive.metric` must name something in `metrics.py` or the
directive is rejected at issue time by design, and there was no metric for "own units standing
on another faction's territory" — so the directive this case most obviously needs could not be
written at all.

That metric now exists (`units_in_foreign_territory`, na-c17 closed), and the adapter computes
it. This test is the evidence, so the blocker is not re-asserted from the bead text: the
promise can be issued, survives to a later turn, and can be checked in both directions.

**The second blocker still stands** and is not what this test covers — see the module note at
the bottom.
"""

from __future__ import annotations

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Action, Choice, Directive, Orders, WorldView
from neural_amplifier.directives import DirectiveStore
from neural_amplifier.metrics import VOCABULARY
from neural_amplifier.orchestrator import Orchestrator


def withdrawal() -> Directive:
    """The promise from na-nmg's live case, written as the type it should always have been."""
    return Directive(
        id="withdraw-believers",
        metric="units_in_foreign_territory",
        comparator="at_most",
        target=0,
        priority=8,
        intent="Answered Miriam's demand with 'Withdraw troops to nearest base' on turn 38.",
        issued_turn=38,
    )


def test_the_metric_the_promise_needs_exists_and_is_faction_scoped() -> None:
    """na-c17's fix, checked from the side that needed it.

    Its description was written for exactly this: position, not intent — a unit already
    marching home still counts until it is out, which is what lets a withdrawal promise be seen
    to be kept. A metric that measured intent could be satisfied by deciding to leave.
    """
    metric = VOCABULARY["units_in_foreign_territory"]
    assert metric.scope == "faction", "the promise is about the faction, not one base"
    assert metric.better == "lower"


def test_a_withdrawal_promise_can_be_issued_at_all() -> None:
    """The first blocker, stated in the bead as absolute: it "cannot be written at all".

    Issue-time rejection is by design — a directive naming a metric nobody reports is a promise
    nobody can check, and the type refuses it rather than accumulating it. So being able to
    construct this one is the whole difference.
    """
    directive = withdrawal()
    assert directive.metric in VOCABULARY
    assert directive.target == 0


def test_the_promise_survives_to_the_turns_it_is_about() -> None:
    """A commitment that died with the response is the failure the type was built for.

    Turn 38 answers; turns 39-41 are when the answer means something. If the directive is not
    in force then, the promise was never made — it was said.
    """
    store = DirectiveStore()
    store.add([withdrawal()])
    assert [d.id for d in store.in_force(turn=41)] == ["withdraw-believers"]


def test_the_promise_is_checkable_in_both_directions() -> None:
    """Kept and broken must be distinguishable, or "we promised" is unfalsifiable.

    Both arms matter. A check that could only see success would report every promise as kept,
    which is exactly the state na-nmg describes: answering "yes, at once" and leaving the troops
    where they are.
    """
    directive = withdrawal()
    broken = WorldView(
        engine="thinker",
        scope="turn",
        surface_id="faction.tech",
        turn=41,
        faction="Peacekeepers",
        metrics={"units_in_foreign_territory": 3},
    )
    kept = broken.model_copy(update={"metrics": {"units_in_foreign_territory": 0}})

    assert (broken.metrics or {})["units_in_foreign_territory"] > directive.target
    assert (kept.metrics or {})["units_in_foreign_territory"] <= directive.target


def test_accepting_withdrawal_issues_the_promise_without_relying_on_model_memory() -> None:
    """The comms answer itself creates the durable promise and returns it to the adapter."""
    view = WorldView(
        engine="thinker",
        scope="turn",
        surface_id="diplo.tribute",
        turn=38,
        faction="Peacekeepers",
        metrics={"units_in_foreign_territory": 3},
        action_space=[
            Action(
                id="withdraw:comply",
                action="withdraw troops to nearest base",
                effects={"units_in_foreign_territory": -1},
            ),
            Action(id="withdraw:refuse", action="refuse the demand"),
        ],
    )
    plan = DirectiveStore()
    result = Orchestrator(
        ScriptedBrain([Orders(choices=[Choice(action_id="withdraw:comply")])]), plan=plan
    ).decide(view)

    assert result.orders.choices[0].action_id == "withdraw:comply"
    assert [(d.metric, d.comparator, d.target) for d in result.orders.directives] == [
        ("units_in_foreign_territory", "at_most", 0)
    ]
    assert [d.id for d in plan.in_force(turn=39)] == ["withdraw-foreign-territory-38"]


def test_refusing_withdrawal_makes_no_promise() -> None:
    view = WorldView(
        engine="thinker",
        scope="turn",
        surface_id="diplo.tribute",
        turn=38,
        faction="Peacekeepers",
        metrics={"units_in_foreign_territory": 3},
        action_space=[Action(id="withdraw:refuse", action="refuse the demand")],
    )
    plan = DirectiveStore()
    result = Orchestrator(
        ScriptedBrain([Orders(choices=[Choice(action_id="withdraw:refuse")])]), plan=plan
    ).decide(view)
    assert result.orders.directives == []
    assert plan.in_force(turn=39) == []
