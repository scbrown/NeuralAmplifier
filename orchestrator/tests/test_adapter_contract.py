"""The Thinker adapter's wire format, checked against the contract.

The adapter emits the contract by hand, from C++, with `snprintf`. Nothing on that side
type-checks it, and the failure mode is quiet: an `extra="allow"` model accepts a misnamed
field and drops it, so a world view missing its action labels still parses and still decides —
just worse, and with no error anywhere. That is how `metrics` sat under `faction_state` for
weeks while every faction-scope directive came back UNMEASURABLE.

So these records are transcribed from the emitters in `thinker/src/neural.cpp` and asserted to
mean what the adapter intends. They are a *pin*, not a proof: they cannot catch the adapter
changing, only the contract changing underneath it. The other half of the check is
`thinker/tests/na_http_test.cpp`, which drives a real POST /decide from the DLL's own client.
"""

from __future__ import annotations

import pytest

from neural_amplifier.contract import Orders, WorldView
from neural_amplifier.metrics import VOCABULARY

# na_write_head + na_build_base_production + na_write_action_space, with the action space
# cut to two entries. Field order and names are the emitters'.
BASE_PRODUCTION = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "base",
    "surface_id": "base.production",
    "turn": 42,
    "faction_id": 1,
    "faction": "Gaians",
    # na_write_head: which run of the game process raised this. Every world view carries it,
    # observations included — it names the process, and a process is what an observation record
    # has too (unlike decision_deadline_ms, which asserts a wait an observation never performs).
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "trace": {"traceparent": "00-0000002a000000010000000107a1c0de-000000010000002b-01"},
    "base_id": 0,
    "base": "Gaia's Landing",
    "x": 13,
    "y": 8,
    "native_choice": -4,
    "has_gov": 0,
    "call_seq": 1,
    "native_choice_name": "Recycling Tanks",
    "base_state": {
        "pop_size": 3,
        "minerals_accumulated": 4,
        "mineral_surplus": 2,
        "nutrient_intake": 5,
        "mineral_intake": 3,
        "energy_intake": 4,
        "eco_damage": 0,
        "worked_tiles": 3,
        "specialists": 0,
        "queue_size": 0,
        "current_item": -4,
        "current_item_name": "Recycling Tanks",
    },
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
        "drone_total": 1,
        "units_in_foreign_territory": 0,
        "mineral_surplus": 2,
        "minerals_remaining": 36,
        "pop_size": 3,
        "turns_to_completion": 18,
    },
    # na_write_history: the contract's `history`, OLDEST FIRST, one entry per base-turn, each
    # attributed to the tier that settled it. Production is re-decided every turn, so without
    # this a brain flips between two defensible options and accumulates nothing.
    #
    # `item` is the action-space id, not the raw engine int, so a past choice can be matched
    # against an option on offer exactly rather than by comparing display names.
    "history": [
        {"turn": 39, "item": "unit:0", "action": "Colony Pod", "tier": "deterministic"},
        {"turn": 40, "item": "facility:4", "action": "Recycling Tanks", "tier": "llm"},
        {"turn": 41, "item": "facility:4", "action": "Recycling Tanks", "tier": "llm"},
    ],
    "action_space": [
        {
            "id": "unit:0",
            "action": "Colony Pod",
            "cost": 30,
            "category": "unit",
            "role": "FOUNDS A NEW BASE elsewhere - does not grow this base",
            "triad": "land",
            "turns_if_switched": 15,
        },
        {
            "id": "facility:4",
            "action": "Recycling Tanks",
            "effect": "Increases minerals and nutrients",
            "cost": 40,
            "maint": 0,
            "category": "facility",
            "turns_if_switched": 20,
            "turns_if_continued": 18,
        },
    ],
    "action_space_size": 2,
    "cost_unit": "minerals",
    # na_write_decision_deadline: conf.llm_timeout_ms, stated so the orchestrator can abandon
    # before the engine does. Emitted on the four *decide* paths only — an observation posts
    # nothing and waits for nobody, so it does not appear in an observation record.
    "decision_deadline_ms": 2500,
    "tier": "llm",
    "applied": "llm",
    "applied_item": 0,
    "applied_item_name": "Colony Pod",
}

# na_observe_base_hurry — the surface that decides *about* an entity rather than between
# entities, and so is the one that needs `subjects`.
BASE_HURRY = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "base",
    "surface_id": "base.hurry",
    "turn": 42,
    "faction_id": 1,
    "faction": "Gaians",
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "trace": {"traceparent": "00-0000002a000000010000000207a1c0de-000000020000002b-01"},
    "base_id": 0,
    "base": "Gaia's Landing",
    "item": "Colony Pod",
    "subjects": ["Colony Pod"],
    "base_state": {
        "minerals_accumulated": 4,
        "mineral_cost_total": 30,
        "minerals_remaining": 26,
        "mineral_surplus": 2,
        "turns_if_waiting": 13,
        "energy_reserves": 82,
        "pop_size": 3,
    },
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
        "drone_total": 1,
        "units_in_foreign_territory": 0,
        "mineral_surplus": 2,
        "minerals_remaining": 26,
        "pop_size": 3,
        "turns_to_completion": 13,
    },
    "action_space": [
        {"id": "hurry:none", "action": "Do not hurry", "cost": 0, "cost_unit": "credits"},
        {
            "id": "hurry:now",
            "action": "Hurry production",
            "cost": 81,
            "cost_unit": "credits",
            "saves_turns": 13,
            "effects": {"energy_reserves": -81, "minerals_remaining": -26},
        },
    ],
    "action_space_size": 2,
    "native_choice": "hurry:none",
    "tier": "deterministic",
    "applied": "native",
}

FACTION_TECH = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "turn",
    "surface_id": "faction.tech",
    "turn": 42,
    "faction_id": 1,
    "faction": "Gaians",
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "trace": {"traceparent": "00-0000002a000000010000000307a1c0de-000000030000002b-01"},
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
        "drone_total": 1,
        "units_in_foreign_territory": 0,
    },
    "tech_accumulated": 0,
    "tech_rate": 40,
    # Research is not production. tech_selection fires only when tech_research_id < 0
    # (tech.cpp:233), so a real selection COMMITS the faction until the tech completes —
    # which is what makes this a long-horizon surface rather than a per-turn pick.
    "research_state": "idle",
    "tech_cost": 280,
    "turns_to_complete": 7,
    "native_choice": 5,
    "native_choice_name": "Centauri Ecology",
    "action_space": [
        {
            "id": "tech:5",
            "action": "Centauri Ecology",
            "category": "tech",
            "ai_weights": {"growth": 3, "tech": 1, "wealth": 0, "power": 0},
        }
    ],
    "action_space_size": 1,
    "tier": "deterministic",
    "applied": "native",
}

#: The adapter caches one decision per base-turn and replays it for the engine's later calls.
#: When the board moved enough that the cached item is no longer buildable, the replay applies
#: the deterministic tier's answer instead — and says so. Before this record existed the log
#: asserted the LLM's choice had been applied while the base quietly built something else.
BASE_PRODUCTION_SUPERSEDED = BASE_PRODUCTION | {
    "call_seq": 2,
    "tier": "deterministic",
    "applied": "native",
    "applied_item": -4,
    "applied_item_name": "Recycling Tanks",
    "superseded_item": 0,
    "fallback_reason": "cached choice became illegal before replay",
}

# na_write_head + na_build_base_retool + na_write_metrics + na_write_base_state. Transcribed
# from the emitter, same as the others. This one is observation-only, so it carries a
# `native_choice` and no brain answer at all — the engine has already chosen by the time the
# record is written.
BASE_RETOOL = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "base",
    "surface_id": "base.retool",
    "turn": 42,
    "faction_id": 1,
    "faction": "Gaians",
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "trace": {"traceparent": "00-0000002a000000010000000307a1c0de-000000030000002b-01"},
    "base_id": 0,
    "base": "Gaia's Landing",
    # NOT `current_item`: base_state carries one of those and it is the queue head. On this
    # surface the two disagree by construction, which is the decision.
    "previous_item": 4,
    "previous_item_name": "Scout Patrol",
    "chosen_item": -4,
    "chosen_item_name": "Recycling Tanks",
    "retool_category_previous": 0,
    "retool_category_chosen": -4,
    "minerals_accumulated": 18,
    "retool_exemption": 10,
    "retool_strictness": 2,
    "penalty_applies": True,
    "subjects": ["Scout Patrol"],
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
        "drone_total": 1,
        "units_in_foreign_territory": 0,
        "mineral_surplus": 2,
        "minerals_remaining": 26,
        "pop_size": 3,
        "turns_to_completion": 13,
    },
    "base_state": {
        "pop_size": 3,
        "minerals_accumulated": 18,
        "mineral_surplus": 2,
        "nutrient_intake": 5,
        "mineral_intake": 3,
        "energy_intake": 4,
        "eco_damage": 0,
        "worked_tiles": 3,
        "specialists": 0,
        "queue_size": 1,
        "current_item": -4,
        "current_item_name": "Recycling Tanks",
    },
    "action_space": [
        {
            "id": "retool:continue",
            "action": "Stay in the current retool category",
            "category": "retool",
        },
        {
            "id": "retool:switch",
            "action": "Cross retool categories, spending 18 banked minerals",
            "category": "retool",
        },
    ],
    "action_space_size": 2,
    "native_choice": "retool:switch",
    "tier": "deterministic",
    "applied": "native",
}

# na_write_head + na_build_base_staple + na_write_metrics + na_write_base_state. The first of
# na-yd4's 27 — the bucket that already has a native AI path, so the fallback exists from the
# first record and invariant 9 needs nothing built first.
BASE_STAPLE = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "base",
    "surface_id": "base.staple",
    "turn": 42,
    "faction_id": 1,
    "faction": "Gaians",
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "trace": {"traceparent": "00-0000002a000000010000000407a1c0de-000000040000002b-01"},
    "base_id": 0,
    "base": "Gaia's Landing",
    # The numbers consider_staple's inner test weighs, as facts rather than as its verdict.
    "drone_total": 3,
    "talent_total": 1,
    "specialist_adjust": 0,
    "nerve_staple_count": 0,
    "drone_riots_active": True,
    "faction_id_former": 1,
    "subjects": ["Gaia's Landing"],
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
        "drone_total": 1,
        "units_in_foreign_territory": 0,
        "mineral_surplus": 2,
        "minerals_remaining": 26,
        "pop_size": 3,
        "turns_to_completion": 13,
    },
    "base_state": {
        "pop_size": 3,
        "minerals_accumulated": 18,
        "mineral_surplus": 2,
        "nutrient_intake": 5,
        "mineral_intake": 3,
        "energy_intake": 4,
        "eco_damage": 0,
        "worked_tiles": 3,
        "specialists": 0,
        "queue_size": 1,
        "current_item": -4,
        "current_item_name": "Recycling Tanks",
    },
    "action_space": [
        {
            "id": "staple:none",
            "action": "Leave the drones unstapled",
            "category": "staple",
        },
        {
            "id": "staple:now",
            "action": "Nerve staple the base",
            "category": "staple",
        },
    ],
    "action_space_size": 2,
    "native_choice": "staple:now",
    "tier": "deterministic",
    "applied": "native",
}

# na_write_head + na_build_corner_market. Turn scope, so faction metrics only and no
# base_state. The highest-stakes surface in na-yd4's bucket: a move toward economic victory.
ECON_CORNER_MARKET = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "turn",
    "surface_id": "econ.corner_market",
    "turn": 42,
    "faction_id": 1,
    "faction": "Gaians",
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "trace": {"traceparent": "00-0000002a000000010000000507a1c0de-000000050000002b-01"},
    "corner_cost": 500,
    # Read BEFORE the deduction, so the record shows the reserve the decision was made
    # against rather than what survived it.
    "energy_credits_before": 820,
    # Non-zero means a corner is ALREADY running, one of the engine's reasons to decline.
    # Kept as its own field so a declined row says WHICH reason.
    "corner_market_cost_existing": 0,
    "corner_market_turn": 0,
    "turns_to_resolve": 20,
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
        "drone_total": 1,
        "units_in_foreign_territory": 0,
    },
    "action_space": [
        {
            "id": "corner:none",
            "action": "Do not corner the energy market",
            "cost": 0,
            "cost_unit": "credits",
            "category": "corner",
        },
        {
            "id": "corner:now",
            "action": "Corner the global energy market",
            "cost": 500,
            "cost_unit": "credits",
            "category": "corner",
            # Declared because it happens THIS turn — the na-co2 distinction between a
            # purchase and a build commitment paid over turns.
            "effects": {"energy_reserves": -500},
        },
    ],
    "action_space_size": 2,
    "native_choice": "corner:now",
    "tier": "deterministic",
    "applied": "native",
}

# na_write_head + na_build_council_call. call_council returns nothing useful, so native_choice
# comes from a STATE TRANSITION the caller observes rather than from a return value.
COUNCIL_CALL = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "turn",
    "surface_id": "council.call",
    "turn": 42,
    "faction_id": 1,
    "faction": "Gaians",
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "trace": {"traceparent": "00-0000002a000000010000000607a1c0de-000000060000002b-01"},
    "eligible": True,
    "council_has_convened": False,
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
        "drone_total": 1,
        "units_in_foreign_territory": 0,
    },
    "action_space": [
        {
            "id": "council:none",
            "action": "Do not convene the council",
            "category": "council",
        },
        {
            "id": "council:call",
            "action": "Convene the Planetary Council",
            "category": "council",
        },
    ],
    "action_space_size": 2,
    "native_choice": "council:none",
    "tier": "deterministic",
    "applied": "native",
}

ALL_RECORDS = [
    BASE_PRODUCTION,
    BASE_HURRY,
    FACTION_TECH,
    BASE_PRODUCTION_SUPERSEDED,
    BASE_RETOOL,
    BASE_STAPLE,
    ECON_CORNER_MARKET,
    COUNCIL_CALL,
]


@pytest.mark.parametrize("record", ALL_RECORDS, ids=[r["surface_id"] for r in ALL_RECORDS])
def test_record_parses_as_a_world_view(record: dict) -> None:
    """Every surface emits something the orchestrator can accept with no translation.

    This is what makes `scripts/decision_stability.py` a straight `model_validate` instead of
    the sixty-line bridge it used to carry.
    """
    world_view = WorldView.model_validate(record)
    assert world_view.engine == "thinker"
    assert world_view.surface_id == record["surface_id"]
    assert world_view.action_space, "an empty action space is not a decision"


@pytest.mark.parametrize("record", ALL_RECORDS, ids=[r["surface_id"] for r in ALL_RECORDS])
def test_actions_carry_a_label_and_an_id(record: dict) -> None:
    """`action`, not `name`.

    The contract calls an action's label `action`; the adapter called it `name` until A1. Because
    `Action` allows extras, a record using the old key still parsed — and every action came out
    labelled with its own id, so the model was choosing between "unit:0" and "facility:4" with
    no idea what either was.
    """
    world_view = WorldView.model_validate(record)
    for action in world_view.action_space:
        assert action.id, "an action with no id cannot be referenced by an order"
        assert action.action, f"{action.id} has no label"
        assert action.action != action.id, f"{action.id} is labelled with its own id"


@pytest.mark.parametrize("record", ALL_RECORDS, ids=[r["surface_id"] for r in ALL_RECORDS])
def test_metric_names_are_in_the_vocabulary(record: dict) -> None:
    """A metric outside the vocabulary is one no directive can be written against.

    Checked in both directions of usefulness: an unknown name is dead weight in the payload, and
    the scope has to match — a base-scope metric on a faction-scope surface would be evaluated
    against an arbitrary base.
    """
    world_view = WorldView.model_validate(record)
    assert world_view.metrics, "a world view with no metrics leaves every directive unmeasurable"
    for name in world_view.metrics:
        assert name in VOCABULARY, f"{name} is not in the metric vocabulary"
        if world_view.scope == "turn":
            assert VOCABULARY[name].scope == "faction", (
                f"{name} is base-scope but {world_view.surface_id} is a faction decision"
            )


@pytest.mark.parametrize("record", ALL_RECORDS, ids=[r["surface_id"] for r in ALL_RECORDS])
def test_declared_effects_use_the_vocabulary(record: dict) -> None:
    """`effects` is the only input to a directive trade-off, so a misnamed key is invisible."""
    world_view = WorldView.model_validate(record)
    for action in world_view.action_space:
        for name in action.effects or {}:
            assert name in VOCABULARY, f"{action.id} declares an effect on unknown metric {name}"


def test_a_build_option_declares_no_effects() -> None:
    """na-co2, pinned at the adapter end. The absence is the fix, so something has to hold it.

    Each of these used to carry `{"minerals_remaining": -cost}` — the item's whole price as a
    withdrawal from a shortfall. It is a category error in both directions: `minerals_remaining`
    is what the base still *owes*, and a build order in this engine spends nothing on the turn
    it is given. Minerals are paid over turns out of `mineral_surplus`, so choosing an item is a
    commitment, not a purchase.

    Continuing the current item moves the shortfall by exactly zero. Switching retargets it by
    an amount that depends on the engine's retool rules, and even computed exactly that number
    is not a cost — abandoning a 220-mineral project for a Scout Patrol would read as
    `minerals_remaining -209`, an improvement. So there is nothing honest to declare, and this
    codebase would rather have an absent field than a wrong one.

    What that costs is real and is recorded rather than hidden: no hop-0 directive retrieval and
    no `Tradeoff` rows on this surface, until the vocabulary gains a metric that is a per-base
    mineral *pool* (na-co2 (c) — `minerals_accumulated` is base state, not a measurement). The
    brain can still weigh price, because every option carries `cost` and both turn estimates.
    """
    world_view = WorldView.model_validate(BASE_PRODUCTION)
    for action in world_view.action_space:
        assert not action.effects, (
            f"{action.id} declares {action.effects}; a build order has no immediate effect to "
            "declare, and -cost against a shortfall is what denied the whole action space"
        )
    assert all(getattr(a, "cost", None) for a in world_view.action_space), (
        "dropping `effects` must not drop the price — `cost` is what the brain compares"
    )


def test_hurry_still_declares_both_of_its_effects() -> None:
    """The contrast that keeps the fix from being read as "effects were a mistake".

    Hurrying spends `energy_reserves` *now* and drives `minerals_remaining` to zero *now*, so
    both deltas are immediate and known — which is what the field is for. Note the mineral leg
    is `-remaining`, the debt itself, and not the item's price: that is the difference between
    declaring an effect you mean and one you don't.
    """
    world_view = WorldView.model_validate(BASE_HURRY)
    hurry = next(a for a in world_view.action_space if a.id == "hurry:now")
    assert hurry.effects == {"energy_reserves": -81, "minerals_remaining": -26}
    state = BASE_HURRY["base_state"]
    assert hurry.effects["minerals_remaining"] == -state["minerals_remaining"], (
        "the mineral leg is the shortfall going to zero, not the item's cost"
    )
    assert hurry.effects["energy_reserves"] == -hurry.cost


def test_hurry_names_its_subject() -> None:
    """The surface that asks a question *about* an entity has to say which entity.

    Retrieval keys off action labels, and this surface's labels are "Hurry production" and
    "Do not hurry" — neither of which is in any datalinks. Without `subjects` it retrieves
    nothing at all, which is measured: it was the least stable surface we had.
    """
    world_view = WorldView.model_validate(BASE_HURRY)
    assert world_view.subjects == ["Colony Pod"]
    assert not any(a.action in (world_view.subjects or []) for a in world_view.action_space), (
        "if the subject were among the action labels, retrieval would not need `subjects`"
    )


def test_traceparent_is_well_formed() -> None:
    """W3C trace context: version-traceid-spanid-flags, with a non-zero trace id.

    A malformed or all-zero traceparent is dropped by collectors rather than rejected, so this
    is the only place it would ever be noticed.
    """
    for record in ALL_RECORDS:
        world_view = WorldView.model_validate(record)
        traceparent = world_view.traceparent()
        assert traceparent, f"{record['surface_id']} sent no traceparent"
        version, trace_id, span_id, flags = traceparent.split("-")
        assert version == "00"
        assert len(trace_id) == 32 and int(trace_id, 16) != 0
        assert len(span_id) == 16 and int(span_id, 16) != 0
        assert flags == "01"


def test_orders_reply_is_what_the_dll_reads() -> None:
    """The DLL extracts exactly one field from the reply: `choices[0].action_id`.

    It does so with a string scan rather than a JSON parser (`na_json_string`), so the field's
    name and nesting are load-bearing in a way a normal client's would not be.
    """
    orders = Orders.model_validate(
        {"schema_version": "0.1", "choices": [{"action_id": "unit:0", "reason": "expand"}]}
    )
    assert orders.choices[0].action_id == "unit:0"
    assert '"action_id":"unit:0"' in orders.model_dump_json().replace(", ", ",")


def test_applied_item_is_recorded_alongside_the_native_choice() -> None:
    """Both sides of the A/B have to survive into the record.

    Comparing the LLM tier against the deterministic tier (na-6db) is only possible if each
    record says what the engine *would* have done as well as what ran. A record that keeps only
    the outcome makes that comparison unrecoverable after the fact.
    """
    assert BASE_PRODUCTION["native_choice"] != BASE_PRODUCTION["applied_item"]
    assert BASE_PRODUCTION["tier"] == "llm"
    assert BASE_PRODUCTION["native_choice_name"] == "Recycling Tanks"
    assert BASE_PRODUCTION["applied_item_name"] == "Colony Pod"


def test_a_superseded_replay_says_what_actually_ran() -> None:
    """A decision that was overtaken by the board must not read as one that was applied.

    The adapter re-asks the engine's own availability tests before replaying a cached choice.
    If the answer changed, the deterministic tier's item runs — and both halves have to survive
    into the record, or the log claims the brain drove a build it did not.
    """
    record = BASE_PRODUCTION_SUPERSEDED
    assert record["tier"] == "deterministic"
    assert record["applied_item"] == record["native_choice"]
    assert record["superseded_item"] != record["applied_item"]
    assert "illegal" in record["fallback_reason"]
    # And it is distinguishable from the original decision, which is the whole point.
    assert record["call_seq"] > BASE_PRODUCTION["call_seq"]
    assert BASE_PRODUCTION["tier"] == "llm"


def test_history_is_oldest_first_and_attributed() -> None:
    """History is only useful if the brain can tell *when* and *who*.

    Oldest first because that is what ``WorldView.history`` documents and what the system prompt
    tells the model to expect. The adapter used to emit newest first under a different name, so a
    model applying the documented reading took the OLDEST entry for the most recent choice.

    Attributed because a run that mixes the LLM and deterministic tiers is otherwise a sequence
    of choices with no provenance, and "why did I pick that" has no answer.
    """
    history = BASE_PRODUCTION["history"]
    turns = [entry["turn"] for entry in history]
    assert turns == sorted(turns), "oldest first"
    assert all(entry["turn"] < BASE_PRODUCTION["turn"] for entry in history), (
        "history is the past; the current decision is not in it"
    )
    # The contract's Literal, exactly. "probe" was emitted here once and would have failed the
    # whole world view rather than one field.
    assert {entry["tier"] for entry in history} <= {"llm", "deterministic", None}
    # One entry per base-turn. The engine calls mod_base_build ~2x per base per turn, so
    # duplicates here would mean the per-turn cache stopped being the single write point.
    assert len(turns) == len(set(turns))


def test_history_populates_the_contract_field_not_merely_the_payload() -> None:
    """The bug this pins (na-wzw): both halves existed and were wired to nothing.

    The adapter emitted ``recent_builds`` while the contract declared ``history``, so
    ``WorldView.history`` was None on every real decision and the system prompt's continuity
    guidance gated on a field that never arrived. Extras still reach the prompt, so nothing
    looked broken — the payload was present, unexplained, and in the opposite order.

    Asserting the parsed field rather than the raw dict is the whole point: a passthrough would
    satisfy ``model_dump()["history"]`` and still leave ``world_view.history`` empty.
    """
    world_view = WorldView.model_validate(BASE_PRODUCTION)
    assert world_view.history, "adapter output must populate the typed contract field"
    assert world_view.history[-1].action == "Recycling Tanks"  # type: ignore[attr-defined]
    assert world_view.history[-1].tier == "llm"


def test_the_engine_deadline_populates_the_contract_field_not_merely_the_payload() -> None:
    """Same pin as `history` above, one bug later (na-t3h), and for the same reason.

    `decision_deadline_ms` is read by name — `AgentBrain` bounds its wait on it and
    `/agent/submit` checks whether it has passed. A passthrough extra would satisfy
    `model_dump()["decision_deadline_ms"]` and leave both of those reading None forever, which
    is precisely the state that produced 66 adapter rows with zero `tier=llm` against
    orchestrator records claiming applied llm decisions. Nothing would have looked broken.

    The `2500` is the adapter's shipped default (`main.h`), not an arbitrary fixture value: an
    unconfigured agent-driven run gets 2.5 seconds, which is why the field had to exist.
    """
    world_view = WorldView.model_validate(BASE_PRODUCTION)
    assert world_view.decision_deadline_ms == 2500
    assert world_view.decision_deadline_seconds() == pytest.approx(2.5)

    # And an adapter that has not been upgraded must stay unbounded rather than acquire a
    # default — inventing one here would abandon decisions the game is still blocked on.
    assert WorldView.model_validate(BASE_HURRY).decision_deadline_seconds() is None


def test_the_run_id_populates_the_contract_field_not_merely_the_payload() -> None:
    """The third field in a row that the orchestrator reads by name (na-bzd), pinned for the
    third time for the reason na-wzw established: an extra satisfies `model_dump()` and leaves
    the attribute reading None forever, and nothing anywhere reports that it never arrived.

    Here that failure would be silent in the worst direction. `DecisionQueue.post` treats a
    missing run id as *cannot tell* and deliberately drops nothing — so a `run_id` that parsed
    as an extra would leave the queue behaving exactly as it did before the fix, offering the
    decisions of a game that has been dead for twenty minutes, with the field visibly present
    on every world view in the log.

    Asserted on all three surfaces because `na_write_head` emits it, not the decide paths: an
    observation record names its process too, and na-observations.jsonl is one file appended
    across every run of the game with nothing else in it to mark where a run ended.
    """
    for payload in (BASE_PRODUCTION, BASE_HURRY, FACTION_TECH):
        world_view = WorldView.model_validate(payload)
        assert world_view.run_id == "68ad1e40-0004e1c8-1a2c"

    # Absent is a legitimate state, not a parse failure: it is where every adapter sits until it
    # is upgraded, and the queue's whole first-decision rule depends on being able to see it.
    silent = {k: v for k, v in BASE_HURRY.items() if k != "run_id"}
    assert WorldView.model_validate(silent).run_id is None


def test_history_items_name_something_the_brain_could_choose() -> None:
    """The convention the field rests on: a past choice is matched against an offered option by
    id, never by display name. An `item` that is not in the action-space vocabulary reduces the
    brain to string-matching names, which is how it misreads state it was handed correctly."""
    world_view = WorldView.model_validate(BASE_PRODUCTION)
    assert world_view.history is not None
    for entry in world_view.history:
        kind, _, num = entry.item.partition(":")
        assert kind in {"unit", "facility"}, entry.item
        assert num.isdigit(), entry.item


def test_every_faction_metric_in_the_vocabulary_is_actually_reported() -> None:
    """The direction nobody was checking, and the one that rots silently.

    `test_metric_names_are_in_the_vocabulary` catches an adapter inventing a name. This catches
    the opposite and worse case: a name sitting in the vocabulary that no adapter reports.
    metrics.py states the rule — "a promise that some adapter reports it, so the honest order of
    work is: emit it from the adapter first, add the name second" — and `drone_total` had been
    breaking it since before anything emitted it.

    Why worse: a directive written against an unreported name is *accepted* at issue time and
    then evaluates UNMEASURABLE forever, which in a record reads as compliance rather than as a
    gap. That was survivable while every directive was hand-written into a plan file. An agent
    can now issue one through `issue_directive`, so an aspirational name is a trap with a
    user-facing path to it.
    """
    faction_metrics = {name for name, m in VOCABULARY.items() if m.scope == "faction"}
    for record in ALL_RECORDS:
        reported = set(record.get("metrics") or {})
        missing = faction_metrics - reported
        assert not missing, (
            f"{record['surface_id']} does not report {sorted(missing)} — either the adapter "
            f"must emit it or metrics.py must drop the name"
        )


def test_every_base_metric_is_reported_on_base_scope_surfaces() -> None:
    """Base-scope names are only *expected* where there is a base to report them for.

    Kept separate from the faction check rather than folded in, because "absent on a turn-scope
    surface" is correct and "absent on a base-scope surface" is the same rot as an aspirational
    name.
    """
    base_metrics = {name for name, m in VOCABULARY.items() if m.scope == "base"}
    for record in ALL_RECORDS:
        if record["scope"] != "base":
            continue
        missing = base_metrics - set(record.get("metrics") or {})
        assert not missing, f"{record['surface_id']} is base-scope but omits {sorted(missing)}"


def test_faction_tech_says_whether_it_is_a_commitment() -> None:
    """The field that turns a tech pick into a long-horizon decision.

    Selection fires only when nothing is being researched, so the choice binds until the tech
    completes. Without `research_state` and `turns_to_complete` the payload looked like a
    one-turn pick, and a brain asked whether the decision "sets direction for future turns" had
    nothing to answer with — measured, it issued no directive on ten consecutive runs.

    `in_progress` is not a decision at all: the probe passes the current target as
    `native_choice`, so a reader has to be able to tell a serialiser test from a selection.
    """
    assert FACTION_TECH["research_state"] in {"idle", "in_progress"}
    assert FACTION_TECH["turns_to_complete"] > 1, "a commitment shorter than a turn is not one"
    world_view = WorldView.model_validate(FACTION_TECH)
    assert world_view.scope == "turn"


def test_per_option_tech_turns_are_omitted_when_they_cannot_differ() -> None:
    """Stock SMAC charges the same for whichever tech is next (tech.cpp:308 asserts it).

    A per-option turns column would then be identical down the list, which invites a brain to
    compare options on a difference that does not exist. It is emitted only under Thinker's
    revised_tech_cost house rule, where the cost genuinely is per-tech.
    """
    for action in FACTION_TECH["action_space"]:
        assert "turns_to_complete" not in action
        assert "cost" not in action


# na_verify_base_production, transcribed from the emitter. Not a world view: a divergence is a
# second event after the decision, so it carries only what identifies the decision it undid.
DIVERGENCE = {
    "surface_id": "base.production",
    "engine": "thinker",
    "scope": "base",
    "turn": 42,
    "base_id": 0,
    "base": "Gaia's Landing",
    "event": "divergence",
    "intended_item": -4,
    "intended_item_name": "Recycling Tanks",
    "applied_item": 12,
    "applied_item_name": "Scout Patrol",
    "fallback_reason": "engine did not keep the applied item",
}


def test_a_divergence_names_both_items() -> None:
    """The record has to say what was decided AND what the base is actually building.

    Either one alone is useless. "The engine dropped our choice" cannot be investigated without
    knowing what it dropped it for, and the applied item alone is indistinguishable from a
    normal deterministic decision.
    """
    assert DIVERGENCE["event"] == "divergence"
    assert DIVERGENCE["intended_item"] != DIVERGENCE["applied_item"]
    for key in ("intended_item", "intended_item_name", "applied_item", "applied_item_name"):
        assert DIVERGENCE[key] not in (None, ""), f"{key} is what makes this investigable"


def test_a_divergence_is_not_mistaken_for_a_decision() -> None:
    """It must not be counted as one, in either direction.

    No `tier` and no `applied`, deliberately: a divergence is not a decision the LLM tier made
    or declined to make, and folding it into either count would move a number that is supposed
    to measure something else. It carries `surface_id` so it can still be attributed, and the
    reader tells the two apart on `event`.
    """
    assert "tier" not in DIVERGENCE
    assert "applied" not in DIVERGENCE
    assert "action_space" not in DIVERGENCE, (
        "no action space means scripts/decision_stability.py skips it, which is why adding "
        "this record type did not break the stability harness"
    )


def test_a_divergence_does_not_claim_a_cause() -> None:
    """The reason says what was observed, not why.

    The whole point of reading state back is that it works without knowing why a choice was
    dropped — it is the check that covers rules nobody has encoded yet. A record that named a
    cause would be guessing, and a guess becomes a fact in someone's analysis three months on.
    """
    assert DIVERGENCE["fallback_reason"] == "engine did not keep the applied item"
    for guess in ("retool", "illegal", "invalid", "rejected"):
        assert guess not in DIVERGENCE["fallback_reason"]


# na_audit_faction_se, transcribed from the emitter. The audit that can actually find
# something: this surface's action space and its apply gate use different predicates.
SE_AUDIT = {
    "surface_id": "faction.se",
    "engine": "thinker",
    "event": "audit",
    "turn": 42,
    "faction_id": 1,
    "rejected_ids": [],
    "hidden_ids": [],
    "offered": 14,
    "rejected": 0,
    "hidden": 0,
}


def test_an_audit_separates_the_two_kinds_of_mismatch() -> None:
    """`rejected` and `hidden` are opposite defects and must not be summed.

    Offered-but-refused is an illegal move waiting to happen — the brain can pick it and the
    engine will not take it. Refused-but-offered is a legal option the brain never sees. One is
    a correctness bug, the other a quality gap, and a single "mismatches" number would hide
    which one a run actually has.
    """
    assert "rejected" in SE_AUDIT and "hidden" in SE_AUDIT
    assert SE_AUDIT["event"] == "audit"
    assert "mismatches" not in SE_AUDIT, "the two must stay separately addressable"


def test_an_audit_lists_ids_and_admits_truncation() -> None:
    """A count alone is not actionable, and a silently capped list is worse than a count.

    The emitter caps each list and emits `*_truncated` with the remainder when it bites, so a
    reader can tell "these are all of them" from "these are the first twelve".
    """
    assert isinstance(SE_AUDIT["rejected_ids"], list)
    assert isinstance(SE_AUDIT["hidden_ids"], list)
    assert len(SE_AUDIT["rejected_ids"]) == SE_AUDIT["rejected"]
    assert len(SE_AUDIT["hidden_ids"]) == SE_AUDIT["hidden"]


def test_an_audit_is_not_a_decision() -> None:
    """Same reasoning as the divergence record: no tier, no applied, no action_space.

    An audit is a check that ran, not a decision anyone made. Counting it as one would inflate
    coverage with records where no choice was ever taken.
    """
    for key in ("tier", "applied", "action_space", "applied_item"):
        assert key not in SE_AUDIT


def test_base_hurry_has_no_audit_because_it_cannot_diverge() -> None:
    """Documents an absence on purpose, so nobody 'fixes' it by adding one back.

    base.hurry's action space and its apply gate call one function (`na_hurry_terms`), so they
    cannot disagree. An audit there compared two identical expressions and could never fail —
    which reads as coverage while testing nothing. The audited surfaces are exactly those where
    two pieces of code independently decide what is legal.
    """
    audited = {"faction.se", "faction.tech", "base.production"}
    assert "base.hurry" not in audited
    assert SE_AUDIT["surface_id"] in audited


def test_retool_names_both_halves_of_the_pair() -> None:
    """A retool record is only readable if the item switched FROM and the item switched TO are
    both present, under names that cannot be confused for each other.

    The trap this pins: `na_write_base_state` emits its own `current_item`, and it is the queue
    head. On this surface the queue head and the previously-produced item disagree *by
    construction* — that disagreement is the entire decision. Emitting the outer one as
    `current_item` too would have produced a record with two same-named fields that reliably
    contradict, and the reader with no way to know which one the categories were computed from.
    """
    world_view = WorldView.model_validate(BASE_RETOOL)
    payload = world_view.model_dump()

    assert payload["previous_item_name"], "nothing to switch away from"
    assert payload["chosen_item_name"], "nothing to switch to"
    assert "current_item" not in payload, "collides with base_state.current_item"

    state = BASE_RETOOL["base_state"]
    assert state["current_item"] == payload["chosen_item"], (
        "base_state's queue head is what the chooser landed on"
    )
    assert payload["previous_item"] != payload["chosen_item"], (
        "a retool record where both halves agree is not a retool"
    )


def test_retool_reports_the_engines_own_answer() -> None:
    """Invariant: this surface's deterministic tier already works, so the record's job is to
    write that tier's answer down (na-lnv).

    Without `native_choice` the record would carry the inputs to a decision and not the
    decision, which is precisely the shape that cannot be A/B'd against a brain (na-6db). The
    answer is also required to AGREE with the categories it was derived from — a `native_choice`
    that contradicts them would be a second, drifting definition of what a crossing is.
    """
    world_view = WorldView.model_validate(BASE_RETOOL)
    payload = world_view.model_dump()

    crossed = payload["retool_category_previous"] != payload["retool_category_chosen"]
    expected = "retool:switch" if crossed else "retool:continue"
    assert payload["native_choice"] == expected

    offered = {a.id for a in world_view.action_space}
    assert payload["native_choice"] in offered, "the engine chose something it was not offered"


def test_retool_is_observation_only() -> None:
    """It is in OBSERVED and must stay out of APPLIED until a decide path exists.

    The record says so itself: `applied: native` means the engine's choice ran. A record
    claiming otherwise would move the coverage number for work nobody did, which is the one
    thing `surfaces.py` exists to prevent.
    """
    from neural_amplifier.surfaces import APPLIED, NO_AI_PATH, OBSERVED

    assert BASE_RETOOL["applied"] == "native"
    assert BASE_RETOOL["tier"] == "deterministic"
    assert "base.retool" in OBSERVED
    assert "base.retool" not in APPLIED
    # Still no *native AI path* in the dialog sense — the penalty is folded into select_build's
    # scoring, which is why it has a working tier and no hook of its own.
    assert "base.retool" in NO_AI_PATH


def test_staple_offers_the_engines_answer_among_its_options() -> None:
    """na-yd4's first surface, and the check that generalises to the other 26.

    Every record in this bucket is an OBSERVATION of a native path, so its whole value is
    `native_choice` — and a native_choice naming something the action space never offered is
    the failure that makes a record unusable for the A/B na-6db wants: there would be nothing
    to compare a brain's answer against.
    """
    world_view = WorldView.model_validate(BASE_STAPLE)
    offered = {a.id for a in world_view.action_space}
    assert offered == {"staple:none", "staple:now"}
    assert BASE_STAPLE["native_choice"] in offered


def test_staple_reports_the_counts_not_the_verdict() -> None:
    """The threshold is the engine's judgement; the counts are what it judged.

    consider_staple fires when drones exceed talents (adjusted), or when a riot is already
    running. Emitting only a derived `should_staple` boolean would bake today's threshold into
    the record, so a later engine change would silently reinterpret every past row.
    """
    for field in ("drone_total", "talent_total", "specialist_adjust", "nerve_staple_count"):
        assert isinstance(BASE_STAPLE[field], int), field
    assert isinstance(BASE_STAPLE["drone_riots_active"], bool)
    assert "should_staple" not in BASE_STAPLE


def test_staple_is_observed_and_not_applied() -> None:
    """The native path is the fallback, which is what makes this bucket safe — and is also
    exactly why observing it must not be mistaken for driving it."""
    from neural_amplifier.surfaces import APPLIED, NO_AI_PATH, OBSERVED

    assert "base.staple" in OBSERVED
    assert "base.staple" not in APPLIED
    # Not one of the 21: consider_staple IS a native AI path. That is the whole distinction
    # between this bucket and na-2mn's.
    assert "base.staple" not in NO_AI_PATH
    assert BASE_STAPLE["applied"] == "native"


def test_corner_market_declares_what_it_actually_spends() -> None:
    """The one surface in this bucket where the cost IS the decision.

    Cornering the energy market is a straight purchase from the reserve, paid on the turn it is
    taken, so `effects` has to declare it — the orchestrator computes every directive trade-off
    from `effects` alone and an undeclared effect is an invisible one.

    Note the contrast this pins: a build option correctly declares NO effects, because minerals
    are paid over turns out of surplus (na-co2). Same repository, opposite answer, and the
    difference is whether the spend happens now.
    """
    world_view = WorldView.model_validate(ECON_CORNER_MARKET)
    by_id = {a.id: a for a in world_view.action_space}
    assert by_id["corner:none"].effects in (None, {})
    assert by_id["corner:now"].effects == {"energy_reserves": -ECON_CORNER_MARKET["corner_cost"]}
    assert by_id["corner:now"].model_dump()["cost"] == ECON_CORNER_MARKET["corner_cost"]


def test_corner_market_shows_the_reserve_the_decision_was_made_against() -> None:
    """`energy_credits_before`, not after.

    The engine deducts the cost inside the same block that decides, so a record built afterwards
    would show the balance the choice PRODUCED and make every affordability check look
    marginal — a corner that cost 500 from 820 would read as 320 against 500, i.e. unaffordable,
    on the very row where it was taken.
    """
    assert ECON_CORNER_MARKET["energy_credits_before"] > ECON_CORNER_MARKET["corner_cost"], (
        "a cornered market must have been affordable at decision time"
    )


def test_council_call_reports_a_transition_not_an_inference() -> None:
    """`call_council` decides internally and hands back nothing useful.

    So the answer is observed as a state transition — convened off before the call, on after.
    `eligible` is `can_call_council`'s own answer and is NOT the choice: the engine can be
    eligible and still decline. This fixture is deliberately that case, because a record that
    inferred the answer from eligibility would get it wrong silently, and in the direction that
    looks like agreement.
    """
    world_view = WorldView.model_validate(COUNCIL_CALL)
    offered = {a.id for a in world_view.action_space}
    assert COUNCIL_CALL["native_choice"] in offered
    assert COUNCIL_CALL["eligible"] is True
    assert COUNCIL_CALL["native_choice"] == "council:none", (
        "eligible-but-declined is the case that proves the two are not the same field"
    )


def test_both_endgame_surfaces_are_observed_not_applied() -> None:
    """A native path exists, so invariant 9 holds — and observation is still not coverage."""
    from neural_amplifier.surfaces import APPLIED, NO_AI_PATH, OBSERVED

    for surface in ("econ.corner_market", "council.call"):
        assert surface in OBSERVED, surface
        assert surface not in APPLIED, surface
        assert surface not in NO_AI_PATH, surface
