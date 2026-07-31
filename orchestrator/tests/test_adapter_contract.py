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
        "mineral_surplus": 2,
        "minerals_remaining": 36,
        "pop_size": 3,
        "turns_to_completion": 18,
    },
    "action_space": [
        {
            "id": "unit:0",
            "action": "Colony Pod",
            "cost": 30,
            "category": "unit",
            "effects": {"minerals_remaining": -30},
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
            "effects": {"minerals_remaining": -40},
            "turns_if_switched": 20,
            "turns_if_continued": 18,
        },
    ],
    "action_space_size": 2,
    "cost_unit": "minerals",
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
    "trace": {"traceparent": "00-0000002a000000010000000307a1c0de-000000030000002b-01"},
    "metrics": {
        "energy_reserves": 82,
        "energy_income": 14,
        "labs_output": 6,
        "base_count": 2,
        "pop_total": 5,
        "military_units": 3,
    },
    "tech_accumulated": 12,
    "tech_rate": 40,
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

ALL_RECORDS = [BASE_PRODUCTION, BASE_HURRY, FACTION_TECH, BASE_PRODUCTION_SUPERSEDED]


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
