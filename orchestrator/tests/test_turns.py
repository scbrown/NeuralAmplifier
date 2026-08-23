"""The turn as a whole: forecast, arrival, answer, outcome.

The load-bearing tests are about the gap between what was forecast and what arrived. An
announcement is made from the previous turn's board, so it is a prediction — and a view that
cannot tell "not yet" from "never coming" is the same trap as an outcome store that reads silence
as success.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.contract import WorldView
from neural_amplifier.doorbell import Doorbell
from neural_amplifier.outcomes import EngineOutcome
from neural_amplifier.service import create_app
from neural_amplifier.turns import ExpectedDecision, TurnAnnouncement, TurnStore

TRACE = "00-0000002a000000010000000107a1c0de-000000010000002b-01"


def announcement(turn: int = 43, bases: int = 3) -> TurnAnnouncement:
    return TurnAnnouncement(
        turn=turn,
        run_id="run-1",
        faction_id=1,
        expected=[
            ExpectedDecision(
                surface_id="base.production", faction_id=1, base_id=i, base=f"Base {i}"
            )
            for i in range(bases)
        ],
    )


def world_view(turn: int = 43, base_id: int = 0, trace: str = TRACE) -> WorldView:
    return WorldView.model_validate(
        {
            "schema_version": "0.1",
            "engine": "thinker",
            "scope": "base",
            "surface_id": "base.production",
            "turn": turn,
            "faction_id": 1,
            "faction": "Gaians",
            "base_id": base_id,
            "base": f"Base {base_id}",
            "trace": {"traceparent": trace},
            "action_space": [{"id": "unit:0", "action": "Scout Patrol"}],
        }
    )


# --- forecast vs reality ------------------------------------------------------


def test_a_forecast_decision_starts_expected_not_raised() -> None:
    store = TurnStore()
    store.announce(announcement(bases=3))
    view = store.view()
    assert view.counts == {"expected": 3}
    assert len(view.unraised) == 3


def test_unraised_names_what_is_missing() -> None:
    """ "51 expected, 47 raised" says something is missing but not which — and which is exactly
    what an agent waiting on one of them needs."""
    store = TurnStore()
    store.announce(announcement(bases=3))
    store.note_raised(world_view(base_id=1))
    view = store.view()
    assert view.counts["raised"] == 1
    assert view.counts["expected"] == 2
    assert "base.production:Base 1" not in view.unraised
    assert "base.production:Base 0" in view.unraised


def test_a_decision_nobody_forecast_is_added_not_dropped() -> None:
    """The forecast is a guess from last turn's board. A decision that arrives unannounced is
    real, and discarding it would make this a record of what we predicted rather than of what is
    happening."""
    store = TurnStore()
    store.announce(announcement(bases=1))
    store.note_raised(world_view(base_id=99))
    view = store.view()
    assert view.counts["raised"] == 1
    assert any(d.base_id == 99 for d in view.decisions)


def test_decisions_before_any_announcement_still_form_a_turn() -> None:
    store = TurnStore()
    store.note_raised(world_view(base_id=0))
    view = store.view()
    assert view.turn == 43
    assert view.announced is False, "an unannounced turn must not claim it was forecast"
    assert view.counts["raised"] == 1


# --- status never walks backwards ---------------------------------------------


def test_a_replay_does_not_reset_an_answered_decision() -> None:
    """A base is asked several times per turn. If a replay reset the slot to `raised`, an
    answered decision would look outstanding and invite a second answer."""
    store = TurnStore()
    store.announce(announcement(bases=1))
    wv = world_view(base_id=0)
    store.note_raised(wv)
    store.note_answered(wv)
    assert store.view().counts["answered"] == 1
    store.note_raised(wv)  # the engine asks again
    assert store.view().counts["answered"] == 1, "status must not walk backwards"


def test_divergence_is_terminal_within_the_turn_view() -> None:
    """A later `applied` for the same decision must not paper over the engine disagreeing."""
    store = TurnStore()
    store.announce(announcement(bases=1))
    wv = world_view(base_id=0)
    store.note_raised(wv)
    store.note_outcome(EngineOutcome(traceparent=TRACE, event="divergence"))
    store.note_outcome(EngineOutcome(traceparent=TRACE, event="applied"))
    assert store.view().counts["diverged"] == 1


def test_outcome_for_an_unknown_trace_is_ignored_quietly() -> None:
    """An outcome from a turn we have evicted, or from another run, must not invent a slot."""
    store = TurnStore()
    store.announce(announcement(bases=1))
    store.note_outcome(EngineOutcome(traceparent="not-a-trace-we-know", event="applied"))
    assert store.view().counts == {"expected": 1}


# --- announcements replace, and turns are bounded -----------------------------


def test_reannouncing_a_turn_replaces_it() -> None:
    """A second announcement means the adapter reached the seam again, so the first describes a
    board that no longer exists. Merging would keep entries from it."""
    store = TurnStore()
    store.announce(announcement(turn=43, bases=5))
    store.announce(announcement(turn=43, bases=2))
    assert len(store.view(43).decisions) == 2


def test_old_turns_are_evicted() -> None:
    store = TurnStore(keep=2)
    for t in (41, 42, 43, 44):
        store.announce(announcement(turn=t, bases=1))
    assert store.turns() == [43, 44]


def test_view_of_an_unknown_turn_is_empty_not_an_error() -> None:
    store = TurnStore()
    view = store.view(999)
    assert view.decisions == []
    assert view.announced is False


# --- over HTTP ----------------------------------------------------------------


def test_turn_endpoint_tracks_a_decision_through_its_life() -> None:
    client = TestClient(create_app())
    posted = client.post("/turn", json=announcement(bases=2).model_dump())
    assert posted.json() == {"turn": 43, "expected": 2}

    view = client.get("/turn").json()
    assert view["counts"] == {"expected": 2}

    # A decision arrives and is answered by the ordinary /decide path.
    client.post("/decide", json=world_view(base_id=0).model_dump(exclude_none=True))
    view = client.get("/turn").json()
    assert view["counts"]["answered"] == 1
    assert view["counts"]["expected"] == 1

    # And the engine reports what it did with it.
    client.post("/outcome", json={"traceparent": TRACE, "event": "applied"})
    view = client.get("/turn").json()
    assert view["counts"]["applied"] == 1


def test_turn_view_is_available_without_an_agent_brain() -> None:
    """Same reasoning as /outcome: this is about the adapter and the game, not the brain."""
    client = TestClient(create_app())
    assert client.get("/health").json()["brain"] != "agent"
    assert client.get("/turn").status_code == 200


# --- extras arrive off the wire, so their types are not guaranteed ------------


def wrong_typed_view(base_id: object) -> WorldView:
    """A world view whose `base_id` extra is whatever an adapter actually sent."""
    payload = world_view().model_dump()
    payload["base_id"] = base_id
    return WorldView.model_validate(payload)


def test_a_correctly_typed_extra_is_read_through_unchanged() -> None:
    """The positive control. Without it, the two tests below would pass just as well against an
    `_extra` that returned None for everything."""
    store = TurnStore()
    store.note_raised(world_view(base_id=7))
    slot = store.view().decisions[0]
    assert slot.base_id == 7
    assert slot.base == "Base 7"


def test_a_WRONG_typed_extra_is_treated_as_absent_never_stored_in_a_typed_field() -> None:
    """`DecisionSlot.base_id` declares `int | None`. Extras come off the wire from an adapter
    with no schema at this seam, so nothing upstream stops a string arriving — and pydantic will
    not coerce it here because `base_id` is an *extra* on WorldView, not a declared field.

    Storing "12" in a field that says int would not raise; it would just make every later read
    quietly wrong, which is the shape mypy was reporting as eight errors. Absent is the honest
    answer: a value that is not an int yields no usable base id.
    """
    store = TurnStore()
    store.note_raised(wrong_typed_view("twelve"))
    slot = store.view().decisions[0]
    assert slot.base_id is None
    assert isinstance(slot.base_id, type(None))


def test_a_wrong_typed_extra_is_LOGGED_so_a_bad_adapter_is_diagnosable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Absent and present-but-malformed produce the same value and have completely different
    causes. Silently coercing would leave a misbehaving adapter undiagnosable."""
    with caplog.at_level(logging.WARNING, logger="neural_amplifier.turns"):
        TurnStore().note_raised(wrong_typed_view("twelve"))
    messages = [r.getMessage() for r in caplog.records]
    assert any("base_id" in m and "str" in m for m in messages), messages


# --- fog: the turn view is one store holding every faction (na-7bk) -----------
#
# The probes below come in PAIRS on purpose, per the slice-1 ruling. An absence on its own
# proves nothing: a filter that returns nothing at all — wrong turn, empty store, a scope typo —
# passes every negative probe perfectly while enforcing no boundary whatever. Each negative
# therefore carries the positive control that separates "the boundary held" from "nothing was
# there".


def agent_brain() -> AgentBrain:
    """An attached-agent brain with no doorbell — `/agent/*` is mounted only for one."""
    return AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=1)


def two_faction_turn(turn: int = 43) -> TurnAnnouncement:
    """One turn as the engine really holds it: two factions, one keyspace.

    Base ids are a single engine-wide sequence, which is exactly why this is a fog problem and
    not a naming one — nothing about base 7 says whose it is.
    """
    return TurnAnnouncement(
        turn=turn,
        run_id="run-1",
        expected=[
            ExpectedDecision(surface_id="base.production", faction_id=1, base_id=1, base="Gaia"),
            ExpectedDecision(
                surface_id="base.production", faction_id=3, base_id=2, base="University Base"
            ),
            ExpectedDecision(
                surface_id="base.production", faction_id=3, base_id=3, base="Saturn Center"
            ),
        ],
    )


def test_a_scoped_turn_view_withholds_the_other_factions_bases() -> None:
    store = TurnStore()
    store.announce(two_faction_turn())

    mine = store.view(43, faction_id=1)
    names = {s.base for s in mine.decisions}
    # NEGATIVE: the other faction's bases are not reachable...
    assert "University Base" not in names
    assert "Saturn Center" not in names
    # ...POSITIVE CONTROL: and my own is, so the filter is filtering rather than emptying.
    assert names == {"Gaia"}

    theirs = store.view(43, faction_id=3)
    assert {s.base for s in theirs.decisions} == {"University Base", "Saturn Center"}


def test_an_unscoped_view_is_still_whole_because_the_observer_needs_it() -> None:
    """The unscoped read is not a leftover; replay and the turn report depend on it.

    Keeping it is only safe because the gate is at the API layer: `/turn` does not claim to be
    an agent surface and `/agent/turn` refuses to run without a faction.
    """
    store = TurnStore()
    store.announce(two_faction_turn())
    assert len(store.view(43).decisions) == 3
    assert store.view(43).unattributed == 0


def test_a_slot_nobody_can_attribute_is_withheld_and_counted() -> None:
    """Fail-closed, and then SAY SO.

    Withholding is the safe direction; withholding in silence is not. An adapter that stopped
    emitting `faction_id` would shrink every agent's forecast to nothing, and from inside the
    plan a base that was filtered out is indistinguishable from a base that is not there. The
    count is the only thing that can tell those apart.
    """
    store = TurnStore()
    store.announce(
        TurnAnnouncement(
            turn=43,
            expected=[
                ExpectedDecision(surface_id="base.production", faction_id=1, base_id=1),
                ExpectedDecision(surface_id="base.production", base_id=9),
            ],
        )
    )
    mine = store.view(43, faction_id=1)
    assert [s.base_id for s in mine.decisions] == [1]
    assert mine.unattributed == 1


def test_the_announcements_own_faction_attributes_its_entries() -> None:
    """An adapter that says whose turn it is once should not have to repeat it per base.

    Without this the fail-closed filter above would withhold the whole forecast from the very
    faction that announced it — correct by the rule and useless in practice.
    """
    store = TurnStore()
    store.announce(
        TurnAnnouncement(
            turn=43,
            faction_id=4,
            expected=[ExpectedDecision(surface_id="base.production", base_id=5, base="Morgan")],
        )
    )
    assert [s.base for s in store.view(43, faction_id=4).decisions] == ["Morgan"]
    assert store.view(43, faction_id=4).unattributed == 0


def test_the_agent_turn_route_refuses_an_unscoped_read() -> None:
    """The gate is a 422, not a default — see the endpoint's own note on why.

    Measured before this landed: an unscoped read returned 49 of another faction's base names.
    """
    client = TestClient(create_app(brain=agent_brain()))
    refused = client.post("/agent/turn", json={"turn": 43})
    assert refused.status_code == 422
    assert "faction" in refused.json()["detail"]


def test_the_agent_turn_route_answers_a_scoped_read() -> None:
    """The positive half. A 422 on everything would pass the test above just as well."""
    client = TestClient(create_app(brain=agent_brain()))
    client.post("/turn", json=two_faction_turn().model_dump())
    view = client.post("/agent/turn", json={"turn": 43, "faction_id": 3}).json()
    assert view["faction_id"] == 3
    assert {d["base"] for d in view["decisions"]} == {"University Base", "Saturn Center"}
    assert not any(d["base"] == "Gaia" for d in view["decisions"])
