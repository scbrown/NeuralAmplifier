"""The vocabulary is a promise that an adapter reports the name. This is the promise.

`metrics.py` holds names; `directives.py` evaluates every standing plan against
`WorldView.metrics`. Nothing in between checks that an adapter ever *fills* that block, and the
failure when it doesn't is silent in the worst way: the directive is accepted at issue time,
looks live in the record's `in_force`, and comes back `unmeasurable` on every decision for the
rest of the game. Unmeasurable is not unsatisfied — it means nobody ever checked — so a plan
that steers nothing is indistinguishable from one being followed unless something asserts the
seam.

What this caught: the Thinker adapter emitted its six faction measurements under a
`faction_state` key while the orchestrator read `metrics`. Both sides were internally
consistent, both were tested, and the chain between them was dead — every directive on a real
world view was unmeasurable regardless of what the adapter reported.
"""

from __future__ import annotations

import json
from pathlib import Path

from neural_amplifier.contract import WorldView
from neural_amplifier.directives import evaluate
from neural_amplifier.metrics import VOCABULARY

#: What `na_write_metrics` in the Thinker adapter (`scbrown/thinker`, `src/neural.cpp`) writes
#: into `WorldView.metrics`. Split the way the adapter splits it: the faction half goes on every
#: surface, the base half only on base-scope ones.
#:
#: Hand-maintained, and deliberately so — the adapter is a different repository in a different
#: language, so this is the seam written down rather than inferred. Changing the adapter without
#: changing this is the mistake; that is why the assertion below is against the whole vocabulary
#: rather than a subset.
THINKER_FACTION = {
    "energy_reserves",
    "energy_income",
    "labs_output",
    "base_count",
    "pop_total",
    "military_units",
    "drone_total",
    # Swept over Vehs the way drone_total is swept over Bases, through whose_territory so an
    # unmet faction's land does not count (na-nmg).
    "units_in_foreign_territory",
    # Swept over Factions, commlink-gated for the same fog reason (na-tit). Measured on a live
    # game after the adapter change: present on 172 of 172 world views across base.hurry,
    # base.production and faction.se, and on 0 of the 862 captured before it.
    "factions_at_war",
}
#: ``turns_to_completion`` is emitted *conditionally* — the adapter omits the key when the base
#: produces no mineral surplus, because a zero there would read as "completes this turn" when
#: the truth is "never". It belongs in this set regardless: the set is the promise that the name
#: is reportable, and the surplus > 0 fixture below is where that promise is actually exercised.
THINKER_BASE = {"pop_size", "mineral_surplus", "minerals_remaining", "turns_to_completion"}


def test_every_name_in_the_vocabulary_is_one_an_adapter_emits() -> None:
    """na-c17's acceptance criterion, as a test rather than a promise.

    A name here that no adapter reports produces directives that are permanently unmeasurable,
    which is the exact failure the closed vocabulary exists to prevent — so the vocabulary must
    not grow ahead of the adapter. If this fails, the fix is to emit the metric, or to drop the
    name; adding it to the sets above without touching `neural.cpp` is how the guarantee dies.
    """
    assert set(VOCABULARY) == THINKER_FACTION | THINKER_BASE


def test_the_scopes_agree_with_the_vocabulary() -> None:
    """A faction-scope name emitted only on base surfaces would be unmeasurable on exactly the
    long-horizon decisions directives are for."""
    assert {n for n in VOCABULARY if VOCABULARY[n].scope == "faction"} == THINKER_FACTION
    assert {n for n in VOCABULARY if VOCABULARY[n].scope == "base"} == THINKER_BASE


#: Every name the vocabulary declares to be a spendable pool. Hand-maintained for the same
#: reason as the sets above: this is the whole content of a promise, so it should cost a
#: deliberate edit to change.
POOLS = {"energy_reserves"}


def test_only_a_declared_pool_can_be_overdrawn() -> None:
    """The na-co2 field, pinned — because the whole value of it is that it is small.

    ``Metric.pool`` is what ``StateGuard`` reads before deciding an order is unaffordable, and
    the criterion is narrow: the reported number *is* the balance available, spending draws it
    down, and zero is a floor the engine enforces. A bank, not a debt and not a rate.

    Nothing else in the vocabulary meets that today. ``minerals_remaining`` is the counter-case
    and the reason the flag exists — it is minerals still *owed*, so reading it as a budget
    denied every build option on every base mid-build. ``mineral_surplus`` and ``energy_income``
    are rates; ``base_count`` and the population counts are censuses. Marking any of them here
    would make this guard deny legal moves, so if this test fails, the question to answer first
    is not "update the set" but "is the new name really a balance something spends".
    """
    assert {n for n in VOCABULARY if VOCABULARY[n].pool} == POOLS


def test_the_pool_flag_defaults_to_the_safe_answer() -> None:
    """A metric added without thinking about this is not a pool.

    The two mistakes are not symmetric. Forgetting to flag a real pool costs an affordability
    check that never runs — the guard stays silent, exactly as it already does for a metric the
    world view omits. Flagging a non-pool denies legal moves and can deny an entire action
    space. So the default has to be the direction that fails quietly.
    """
    from neural_amplifier.metrics import Metric

    assert Metric(name="x", scope="base", unit="things", better=None, description="").pool is False


def _fixture() -> WorldView:
    path = Path(__file__).parent / "fixtures" / "thinker_base_production.json"
    return WorldView.model_validate(json.loads(path.read_text()))


#: Faction-scope names the adapter CANNOT report on a base-scope surface, read from the
#: vocabulary's own `accumulated` field rather than listed again here. The engine re-sums these
#: during the production phase and base decisions fire inside that window, so the adapter omits
#: the key instead of reporting a partial total (na-an6). Measured live: 3 of 3 faction.se
#: records carry them, 0 of 169 base-scope ones (na-095).
ACCUMULATED = {n for n, m in VOCABULARY.items() if m.accumulated}


def test_a_thinker_world_view_reports_the_whole_vocabulary() -> None:
    """The fixture is the contract seam (`docs/building-and-testing.md` §5).

    A base-scope Thinker world view reports both halves MINUS the accumulated names — and that
    subtraction is the whole of na-095. The fixture used to carry `energy_income` and
    `labs_output` on a base.production record, which the adapter cannot emit there, so this
    assertion passed against a record no game could produce. A hand-written fixture is exactly
    where that drift hides: nothing regenerates it, so it agrees with whatever it agreed with
    when it was written.
    """
    reported = set(_fixture().metrics or {})
    assert reported == (THINKER_FACTION | THINKER_BASE) - ACCUMULATED
    assert ACCUMULATED, "an empty exception set would make the subtraction vacuous"


def test_the_accumulated_names_are_reportable_somewhere() -> None:
    """The other half, without which the exemption above is just a deletion.

    A name exempted everywhere is a name nothing checks, which is the aspirational-name failure
    this file exists to prevent, wearing an exemption as a disguise. These must still be
    emitted — on faction-scope surfaces, where the totals are finished.
    """
    assert ACCUMULATED <= THINKER_FACTION
    assert {"energy_income", "labs_output"} == ACCUMULATED, (
        "a new accumulated name needs its own live measurement before it is exempted here"
    )


def test_directives_on_a_real_world_view_are_measurable() -> None:
    """End to end, and the assertion that actually fails when the seam breaks.

    `satisfied is None` is the unmeasurable state. Before the adapter wrote `metrics`, every
    one of these came back None — which the record reports as a gap in the *adapter*, and which
    nothing in the orchestrator's own test suite could see.
    """
    from neural_amplifier.contract import Directive

    plan = [
        Directive(
            id=f"hold-{name}",
            intent=f"A plan written against {name}.",
            metric=name,
            comparator="at_least",
            target=1.0,
        )
        for name in sorted(VOCABULARY)
    ]
    statuses = evaluate(plan, _fixture())

    unmeasurable = {s.directive.metric for s in statuses if s.satisfied is None}
    # Exactly the accumulated names, and not one more. `satisfied is None` is the unmeasurable
    # state, and on a base-scope view it is the CORRECT answer for these two — the number does
    # not exist yet, and reporting a directive as satisfied against a total that is mid-sum
    # would be the flattering failure this whole seam is built to refuse.
    assert unmeasurable == ACCUMULATED


# --- the withdrawal promise (na-nmg) ----------------------------------------


def test_a_withdrawal_promise_can_be_written_as_a_directive() -> None:
    """na-nmg's first blocker, lifted.

    The live case, turn 38: Miriam asks the Peacekeepers to withdraw from Believer territory
    and "Withdraw troops to nearest base" is a promise about LATER turns. A directive is the
    type that carries a commitment past the dialog, and `Directive.metric` must name something
    in the vocabulary or it is refused at issue time — so with no name for "units in foreign
    territory" the one directive that decision most obviously needs could not be written at all.
    The answer was accepted, the troops stayed, and nothing noticed.

    This asserts only that it is now *expressible and measurable*. Whether a comms surface
    actually issues it is the rest of na-nmg and needs the dialog interception (na-4lr).
    """
    from neural_amplifier.contract import Directive
    from neural_amplifier.directives import accept, evaluate

    promise = Directive(
        id="withdraw-from-believer-land",
        intent="We told Miriam we would pull back at once. Honour it before it costs the treaty.",
        metric="units_in_foreign_territory",
        comparator="at_most",
        target=0,
        priority=8,
    )
    world_view = _fixture().model_copy(
        update={"metrics": {**(_fixture().metrics or {}), "units_in_foreign_territory": 3}}
    )

    accepted, rejected = accept([promise], world_view)
    assert rejected == [], rejected
    assert [d.id for d in accepted] == ["withdraw-from-believer-land"]

    (status,) = evaluate(accepted, world_view)
    assert status.current == 3
    assert status.satisfied is False  # three units still standing on her land


def test_the_promise_reads_satisfied_once_the_troops_are_out() -> None:
    """The other half, and the reason the metric is position rather than intent: it has to be
    able to go to zero, or "kept" is unrepresentable and the directive nags forever."""
    from neural_amplifier.contract import Directive
    from neural_amplifier.directives import evaluate

    promise = Directive(
        id="withdraw",
        intent="honour the withdrawal",
        metric="units_in_foreign_territory",
        comparator="at_most",
        target=0,
    )
    home = _fixture().model_copy(
        update={"metrics": {**(_fixture().metrics or {}), "units_in_foreign_territory": 0}}
    )

    (status,) = evaluate([promise], home)
    assert status.current == 0
    assert status.satisfied is True
