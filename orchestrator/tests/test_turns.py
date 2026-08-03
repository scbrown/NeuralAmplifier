"""The turn as a whole: forecast, arrival, answer, outcome.

The load-bearing tests are about the gap between what was forecast and what arrived. An
announcement is made from the previous turn's board, so it is a prediction — and a view that
cannot tell "not yet" from "never coming" is the same trap as an outcome store that reads silence
as success.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from neural_amplifier.contract import WorldView
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
