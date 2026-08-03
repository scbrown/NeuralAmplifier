"""Engine outcomes: what the game did, as distinct from what we decided.

The tests that matter here are the negative ones. A store that records and returns outcomes is
easy; the value is in refusing to let silence read as success, which is the failure this whole
module exists to remove.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from neural_amplifier.outcomes import EngineOutcome, OutcomeStore
from neural_amplifier.service import create_app

TRACE = "00-0000002a000000010000000107a1c0de-000000010000002b-01"
TRACE2 = "00-0000002a000000010000000207a1c0de-000000020000002b-01"


def _applied(trace: str = TRACE, **kw: object) -> EngineOutcome:
    return EngineOutcome(
        traceparent=trace,
        surface_id="base.production",
        turn=42,
        base_id=0,
        base="Gaia's Landing",
        tier="llm",
        applied="llm",
        applied_item=-4,
        applied_item_name="Recycling Tanks",
        **kw,  # type: ignore[arg-type]
    )


# --- the point of the module ------------------------------------------------


def test_an_unreported_decision_is_unknown_not_applied() -> None:
    """Silence is not success.

    If this ever returns "applied", every failure mode the adapter can have — a build behind, a
    dropped socket, a crashed game — becomes indistinguishable from a working one.
    """
    store = OutcomeStore()
    assert store.get(TRACE).status == "unknown"
    assert store.get(TRACE).outcomes == []


def test_divergence_wins_regardless_of_arrival_order() -> None:
    """A divergence always means "the game does not have what you ordered".

    Reading the LAST report would be right today, because a divergence always follows an apply —
    and wrong the first time two reports race. The question is not "what happened most recently"
    but "is what I ordered what the game has".
    """
    store = OutcomeStore()
    store.record(_applied())
    store.record(
        EngineOutcome(
            traceparent=TRACE,
            event="divergence",
            intended_item=-4,
            intended_item_name="Recycling Tanks",
            applied_item=1,
            applied_item_name="Scout Patrol",
            fallback_reason="engine did not keep the applied item",
        )
    )
    assert store.get(TRACE).status == "diverged"

    backwards = OutcomeStore()
    backwards.record(EngineOutcome(traceparent=TRACE, event="divergence"))
    backwards.record(_applied())
    assert backwards.get(TRACE).status == "diverged", "order of arrival must not change the verdict"


def test_both_reports_are_kept() -> None:
    """Collapsing them would lose the only interesting case: something UNDID a success."""
    store = OutcomeStore()
    store.record(_applied())
    store.record(EngineOutcome(traceparent=TRACE, event="divergence"))
    assert [o.event for o in store.get(TRACE).outcomes] == ["applied", "divergence"]


# --- the cursor -------------------------------------------------------------


def test_cursor_advances_even_when_nothing_is_returned() -> None:
    """Otherwise a poller re-reads the same tail forever."""
    store = OutcomeStore()
    store.record(_applied())
    cursor, fresh = store.since(0)
    assert len(fresh) == 1
    again_cursor, again = store.since(cursor)
    assert again == []
    assert again_cursor == cursor


def test_evicted_outcomes_do_not_wedge_the_cursor() -> None:
    """A decision aged out of the window must still advance the cursor.

    Leaving its sequence number behind would park a poller on something that can never arrive.
    """
    store = OutcomeStore(capacity=2)
    for i in range(6):
        store.record(_applied(trace=f"trace-{i}"))
    cursor, fresh = store.since(0, limit=100)
    assert cursor == 6, "cursor reflects every report, including evicted ones"
    assert len(fresh) <= 2


def test_capacity_bounds_the_store() -> None:
    store = OutcomeStore(capacity=3)
    for i in range(10):
        store.record(_applied(trace=f"trace-{i}"))
    assert store.stats()["decisions"] == 3
    assert store.stats()["reports"] == 10


def test_stats_count_decisions_not_reports() -> None:
    """Two reports about one decision is one diverged decision, not two."""
    store = OutcomeStore()
    store.record(_applied())
    store.record(EngineOutcome(traceparent=TRACE, event="divergence"))
    store.record(_applied(trace=TRACE2))
    stats = store.stats()
    assert stats == {"decisions": 2, "applied": 1, "diverged": 1, "reports": 3}


def test_unmodelled_fields_survive() -> None:
    """The adapter writes JSON by hand from C++. A field we have not modelled must not 422 —
    losing an outcome to a schema disagreement restores the blindness this exists to remove."""
    o = EngineOutcome(traceparent=TRACE, some_future_field=7)  # type: ignore[call-arg]
    assert o.model_dump()["some_future_field"] == 7


# --- over HTTP --------------------------------------------------------------


def test_outcome_endpoint_records_and_reads_back() -> None:
    client = TestClient(create_app())
    posted = client.post("/outcome", json=_applied().model_dump(exclude_none=True))
    assert posted.status_code == 200
    assert posted.json()["recorded"] == 1

    got = client.get(f"/outcome/{TRACE}")
    assert got.status_code == 200
    assert got.json()["status"] == "applied"


def test_unknown_traceparent_is_200_not_404() -> None:
    """A 404 invites a caller to treat "no answer yet" as "nothing went wrong"."""
    client = TestClient(create_app())
    got = client.get("/outcome/never-reported")
    assert got.status_code == 200
    assert got.json()["status"] == "unknown"


def test_outcome_is_mounted_without_an_agent_brain() -> None:
    """Unlike /agent/*, this is about the ADAPTER, not the brain.

    Gating it on AgentBrain would mean the measurement lanes could never see a divergence.
    """
    client = TestClient(create_app())
    assert client.get("/health").json()["brain"] != "agent"
    assert client.post("/outcome", json={"traceparent": TRACE}).status_code == 200


# --- the adapter's wire format ----------------------------------------------
#
# Transcribed from the emitters in `thinker/src/neural.cpp` (na_decide_base_production and
# na_verify_base_production), field order and names as the adapter writes them. A *pin*, not a
# proof — exactly as test_adapter_contract.py says of itself: it catches the contract moving
# under the adapter, not the adapter changing.

APPLIED_WIRE = {
    "traceparent": TRACE,
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "surface_id": "base.production",
    "event": "applied",
    "turn": 42,
    "base_id": 0,
    "base": "Gaia's Landing",
    "tier": "llm",
    "applied": "llm",
    "applied_item": -4,
    "applied_item_name": "Recycling Tanks",
}

DIVERGENCE_WIRE = {
    "traceparent": TRACE,
    "run_id": "68ad1e40-0004e1c8-1a2c",
    "surface_id": "base.production",
    "event": "divergence",
    "turn": 42,
    "base_id": 0,
    "base": "Gaia's Landing",
    "intended_item": -4,
    "intended_item_name": "Recycling Tanks",
    "applied_item": 1,
    "applied_item_name": "Scout Patrol",
    "fallback_reason": "engine did not keep the applied item",
}


def test_adapter_wire_records_parse_and_mean_what_they_say() -> None:
    for record in (APPLIED_WIRE, DIVERGENCE_WIRE):
        parsed = EngineOutcome.model_validate(record)
        assert parsed.traceparent == TRACE
        assert parsed.surface_id == "base.production"
    assert EngineOutcome.model_validate(APPLIED_WIRE).event == "applied"
    assert EngineOutcome.model_validate(DIVERGENCE_WIRE).event == "divergence"


def test_the_adapters_two_reports_compose_into_diverged() -> None:
    """The pair as a real base-turn produces them: applied, then undone."""
    client = TestClient(create_app())
    assert client.post("/outcome", json=APPLIED_WIRE).status_code == 200
    assert client.post("/outcome", json=DIVERGENCE_WIRE).status_code == 200
    got = client.get(f"/outcome/{TRACE}").json()
    assert got["status"] == "diverged"
    assert [o["event"] for o in got["outcomes"]] == ["applied", "divergence"]
    # The divergence must still carry what we ASKED for; without it a reader knows something
    # went wrong but not what was lost.
    assert got["outcomes"][1]["intended_item_name"] == "Recycling Tanks"
    assert got["outcomes"][1]["applied_item_name"] == "Scout Patrol"


def test_outcomes_feed_paginates() -> None:
    client = TestClient(create_app())
    for i in range(3):
        client.post("/outcome", json=_applied(trace=f"t-{i}").model_dump(exclude_none=True))
    first = client.get("/outcomes?cursor=0&limit=2").json()
    assert len(first["outcomes"]) == 2
    second = client.get(f"/outcomes?cursor={first['cursor']}").json()
    assert len(second["outcomes"]) == 1
    assert second["stats"]["decisions"] == 3
