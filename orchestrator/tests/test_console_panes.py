"""The console's SEATS and POLICY panes (aegis-8fye.4 slice 2, ``seats-and-policy.md`` §7).

What has to hold: the two APIs follow the /events token rule (seat token -> own faction only,
spectator -> all, no token -> 401) and a spectator read spoils the run once; a policy change and
a refused change are each recorded exactly once, in the JSONL before the stream; the change log
is the record, never rebuilt from current state; and a field with no source reads UNKNOWN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.config import Seat
from neural_amplifier.dashboard import UNKNOWN, policy_view, seats_view
from neural_amplifier.decisions import DecisionLog, DecisionRecord
from neural_amplifier.faction_policy import KnobError, PolicyStore
from neural_amplifier.live import LiveStream
from neural_amplifier.seats import SEAT_HEADER, SeatRouter
from neural_amplifier.service import create_app
from neural_amplifier.telemetry import Emitter, sinks_for


def record(seat: int | None, **extra: Any) -> DecisionRecord:
    base: dict[str, Any] = {
        "turn": 1,
        "faction": "F",
        "engine": "thinker",
        "scope": "base",
        "tier": "llm",
        "world_view_hash": "h",
        "action_space_size": 2,
        "seat": seat,
    }
    base.update(extra)
    return DecisionRecord(**base)


@pytest.fixture
def seated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    monkeypatch.setenv("NA_RUN_DIR", str(tmp_path))
    monkeypatch.delenv("NA_SPECTATOR_TOKEN", raising=False)
    router = SeatRouter(
        {3: Seat(3, "three", "scripted", budget_usd=2.5), 5: Seat(5, "five", "scripted")}
    )
    log = DecisionLog(tmp_path / "orch" / "decisions.jsonl")
    app = create_app(brain=router, log=log)
    return app, TestClient(app), tmp_path, log


def _set(client: TestClient, token: str, knob: str, value: Any, reason: str) -> int:
    body = {"knob": knob, "value": value, "reason": reason}
    return client.post("/agent/policy", json=body, headers={SEAT_HEADER: token}).status_code


# --------------------------------------------------------------------------- token rule


@pytest.mark.parametrize("path", ["/dashboard/api/seats", "/dashboard/api/policy"])
def test_no_token_or_a_forged_one_gets_401(seated, path: str) -> None:
    app, client, _, _ = seated
    assert client.get(path).status_code == 401
    assert client.get(path, params={"token": "nope"}).status_code == 401
    assert client.get(path, headers={SEAT_HEADER: "forged"}).status_code == 401
    assert app.state.run_record.observer_spoiled_at is None


def test_a_seat_token_never_sees_another_factions_knobs_or_log(seated) -> None:
    app, client, _, log = seated
    t3, t5 = app.state.seat_tokens.tokens[3], app.state.seat_tokens.tokens[5]
    assert _set(client, t5, "focus.fight", 1, "five-secret-reason") == 200
    assert _set(client, t5, "focus.fight", 9, "five-refused") == 422
    assert _set(client, t3, "focus.tech", 1, "three goes tech") == 200
    log.write(record(5, seat_principal="five", model="m5", latency_ms=50))
    log.write(record(3, seat_principal="three", model="m3", latency_ms=70))

    for kind, path in (("header", "/dashboard/api/policy"), ("query", "/dashboard/api/policy")):
        response = (
            client.get(path, headers={SEAT_HEADER: t3})
            if kind == "header"
            else client.get(path, params={"token": t3})
        )
        assert response.status_code == 200
        body = response.json()
        assert body["view"] == "player"
        assert [row["faction_id"] for row in body["policy"]] == [3]
        assert body["policy"][0]["knobs"] == {"focus.tech": 1}
        assert "five" not in response.text and "focus.fight" not in response.text

    seats = client.get("/dashboard/api/seats", headers={SEAT_HEADER: t3})
    assert [row["seat"] for row in seats.json()["seats"]] == [3]
    assert "m5" not in seats.text and "five" not in seats.text
    # A player view is not a spectator view.
    assert app.state.run_record.observer_spoiled_at is None


def test_the_spectator_sees_every_seat_and_spoils_the_run_once(seated) -> None:
    app, client, run_dir, _ = seated
    spectator = app.state.viewers.spectator
    t5 = app.state.seat_tokens.tokens[5]
    assert _set(client, t5, "focus.fight", 1, "push") == 200

    policy = client.get("/dashboard/api/policy", params={"token": spectator}).json()
    assert policy["view"] == "spectator"
    assert {row["faction_id"] for row in policy["policy"]} == {3, 5}
    stamp = json.loads((run_dir / "run.json").read_text())["observer_spoiled_at"]
    assert stamp == app.state.run_record.observer_spoiled_at

    seats = client.get("/dashboard/api/seats", headers={SEAT_HEADER: spectator}).json()
    assert [row["seat"] for row in seats["seats"]] == [3, 5]
    assert app.state.run_record.spoil() is False  # already written, by the first read only
    assert json.loads((run_dir / "run.json").read_text())["observer_spoiled_at"] == stamp


# --------------------------------------------------------------------------- the change log


def test_a_change_and_a_refusal_each_appear_once_jsonl_before_stream(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "decisions.jsonl")
    live = LiveStream()
    sub = live.subscribe(None)
    on_disk_when_published: list[int] = []
    original = sub.offer

    def offer(item: tuple[int, str, dict[str, Any]]) -> None:
        lines = log.policy_path.read_text().splitlines() if log.policy_path.exists() else []
        on_disk_when_published.append(len(lines))
        original(item)

    sub.offer = offer  # type: ignore[method-assign]
    emitter = Emitter(*sinks_for(log, live=live))
    store = PolicyStore()
    store.observer = emitter.emit_policy

    store.set_policy(3, "focus.growth", 1, "  grow while small ", set_by="skill", turn=7)
    with pytest.raises(KnobError):
        store.set_policy(3, "focus.growth", 5, "too much", set_by="directive", turn=8)

    rows = log.read_policy()
    assert [(r["outcome"], r["new"], r["turn"], r["set_by"]) for r in rows] == [
        ("set", 1, 7, "skill"),
        ("refused", 5, 8, "directive"),
    ]
    assert rows[0]["old"] is None and rows[1]["old"] == 1
    assert "outside" in rows[1]["refusal"]
    # Each published once, and only after its line was on disk.
    assert on_disk_when_published == [1, 2]
    events = [(kind, payload["outcome"]) for _, kind, payload in sub._events]
    assert events == [("policy", "set"), ("policy", "refused")]
    assert emitter.healthy
    # A refusal changed nothing in force.
    assert store.knobs(3) == {"focus.growth": 1}


def test_reasons_survive_the_round_trip(seated) -> None:
    app, client, _, log = seated
    t3 = app.state.seat_tokens.tokens[3]
    reason = 'hold the "north" line — then push; ünïcode & <tags>'
    assert _set(client, t3, "strategy.land_power", True, reason) == 200
    assert _set(client, t3, "strategy.land_power", None, "back to defaults") == 200

    [row] = client.get("/dashboard/api/policy", headers={SEAT_HEADER: t3}).json()["policy"]
    assert [c["reason"] for c in row["changes"]] == [reason, "back to defaults"]
    assert [(c["old"], c["new"]) for c in row["changes"]] == [(None, True), (True, None)]
    assert {c["set_by"] for c in row["changes"]} == {"directive"}
    assert [c["reason"] for c in log.read_policy()] == [reason, "back to defaults"]
    assert row["knobs"] == {}


def test_history_is_never_invented_from_current_state() -> None:
    store = PolicyStore()  # no observer: nothing was recorded
    store.set_policy(4, "focus.tech", 1, "set before any log existed")
    no_log = policy_view(store, None, set(), None)
    assert no_log["policy"] == [
        {
            "faction_id": 4,
            "knobs": {"focus.tech": 1},
            "delivered_hash": store.hash_for(4),
            "changes": UNKNOWN,
        }
    ]
    # With a log that holds nothing for it, the knob is in force and the log is empty.
    assert policy_view(store, [], set(), None)["policy"][0]["changes"] == []


def test_the_wire_cannot_claim_a_skill_set_it(seated) -> None:
    app, client, _, log = seated
    t3 = app.state.seat_tokens.tokens[3]
    body = {"knob": "focus.tech", "value": 1, "reason": "r", "set_by": "skill"}
    assert client.post("/agent/policy", json=body, headers={SEAT_HEADER: t3}).status_code == 422
    assert log.read_policy() == []


# --------------------------------------------------------------------------- seats


def test_a_missing_source_renders_unknown_never_zero() -> None:
    view = seats_view([], {3: None}, None)
    [row] = view["seats"]
    assert row["decisions"] == 0
    assert row["driver"] == {"principal": UNKNOWN, "model": UNKNOWN}
    assert row["standing_directives"] == UNKNOWN
    assert row["turn_plan"] == {"hits": UNKNOWN, "misses": UNKNOWN}
    assert row["latency_ms"]["p50"] == UNKNOWN and row["latency_ms"]["p95"] == UNKNOWN
    assert row["degrade_rate"] == UNKNOWN
    assert row["spend_usd"] == UNKNOWN
    assert row["budget_usd"] == UNKNOWN

    # Records that carry no latency, no cost and no plan store still leave those UNKNOWN.
    rows = seats_view([record(3, plan={"plan_absent": True})], {}, 3)["seats"]
    assert rows[0]["latency_ms"]["p50"] == UNKNOWN
    assert rows[0]["spend_usd"] == UNKNOWN
    assert rows[0]["standing_directives"] == UNKNOWN
    assert rows[0]["degrade_rate"] == 0.0  # measured: one decision, not degraded


def test_seat_figures_come_from_the_decision_log() -> None:
    records = [
        record(3, latency_ms=ms, degraded=ms == 400, tier="plan" if ms == 10 else "llm")
        for ms in (10, 20, 30, 40, 400)
    ]
    records[-1] = record(
        3,
        latency_ms=400,
        degraded=True,
        seat_principal="three",
        model="m",
        plan={"in_force": ["bank-for-expansion"]},
        cost_usd=0.25,
    )
    [row] = seats_view(records, {3: 2.5}, None)["seats"]
    assert row["latency_ms"] == {"p50": 30, "p95": 400, "samples": 5}
    assert row["degrade_rate"] == 0.2
    assert row["turn_plan"]["hits"] == 1
    assert row["turn_plan"]["misses"] == UNKNOWN  # retired in memory, not in the record
    assert row["standing_directives"] == ["bank-for-expansion"]
    assert row["driver"] == {"principal": "three", "model": "m"}
    assert (row["spend_usd"], row["budget_usd"]) == (0.25, 2.5)


def test_unseated_decisions_are_the_spectators_alone() -> None:
    records = [record(None), record(3)]
    assert [r["seat"] for r in seats_view(records, {}, None)["seats"]] == [3, None]
    assert [r["seat"] for r in seats_view(records, {}, 3)["seats"]] == [3]


def test_the_page_carries_the_seats_and_policy_panes() -> None:
    page = TestClient(create_app()).get("/dashboard").text
    assert "<h2>SEATS</h2>" in page and "<h2>POLICY</h2>" in page
    assert "/dashboard/api/seats" in page and "/dashboard/api/policy" in page
    assert "loadConsole()" in page
