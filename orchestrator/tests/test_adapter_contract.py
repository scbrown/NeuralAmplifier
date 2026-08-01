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
        "drone_total": 1,
        "mineral_surplus": 2,
        "minerals_remaining": 36,
        "pop_size": 3,
        "turns_to_completion": 18,
    },
    # na_write_history: newest first, one entry per base-turn, each attributed to the tier
    # that settled it. Production is re-decided every turn, so without this a brain flips
    # between two defensible options and accumulates nothing.
    "recent_builds": [
        {"turn": 41, "item": -4, "action": "Recycling Tanks", "tier": "llm"},
        {"turn": 40, "item": -4, "action": "Recycling Tanks", "tier": "llm"},
        {"turn": 39, "item": 0, "action": "Colony Pod", "tier": "deterministic"},
    ],
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
        "drone_total": 1,
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
        "drone_total": 1,
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


def test_recent_builds_are_newest_first_and_attributed() -> None:
    """History is only useful if the brain can tell *when* and *who*.

    Newest first because a brain reading top-down should meet the most relevant entry first.
    Attributed because a run that mixes the LLM and deterministic tiers is otherwise a sequence
    of choices with no provenance, and "why did I pick that" has no answer.
    """
    history = BASE_PRODUCTION["recent_builds"]
    turns = [entry["turn"] for entry in history]
    assert turns == sorted(turns, reverse=True), "newest first"
    assert all(entry["turn"] < BASE_PRODUCTION["turn"] for entry in history), (
        "history is the past; the current decision is not in it"
    )
    assert {entry["tier"] for entry in history} <= {"llm", "deterministic", "probe"}
    # One entry per base-turn. The engine calls mod_base_build ~2x per base per turn, so
    # duplicates here would mean the per-turn cache stopped being the single write point.
    assert len(turns) == len(set(turns))


def test_history_survives_as_an_engine_dependent_passthrough() -> None:
    """`recent_builds` is not a contract field, and does not need to be.

    WorldView allows extras and the whole payload is what reaches the prompt, so an adapter can
    add a genuinely useful block without a contract change. What it must not do is arrive in a
    shape the orchestrator would reject.
    """
    world_view = WorldView.model_validate(BASE_PRODUCTION)
    carried = world_view.model_dump()["recent_builds"]
    assert carried[0]["action"] == "Recycling Tanks"
    assert carried[0]["tier"] == "llm"


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
