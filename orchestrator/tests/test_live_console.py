"""The console's live stream (aegis-8fye.4, ``docs/seats-and-policy.md`` §7, slice 1).

What has to hold: a viewer can never stall or break ``emit()``; the JSONL record of truth is
written before any viewer hears of a decision; filtering is done on the server, so a seat token
never receives another faction's event; no token means 401 and no stream; and the spectator
view marks the run record ``observer_spoiled`` exactly once.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.config import Seat
from neural_amplifier.contract import WorldView
from neural_amplifier.dashboard import event_stream
from neural_amplifier.decisions import DecisionLog, DecisionRecord
from neural_amplifier.live import LiveStream, RunRecord, parse_sse, sse_frame
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.seats import SEAT_HEADER, SeatRouter
from neural_amplifier.service import create_app
from neural_amplifier.telemetry import Emitter, sinks_for


def view(faction_id: int, turn: int = 12) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.production",
        "turn": turn,
        "faction": f"F{faction_id}",
        "faction_id": faction_id,
        "base_id": 100 + faction_id,
        "action_space": [
            {"id": "unit:0", "action": "Colony Pod"},
            {"id": "facility:4", "action": "Recycling Tanks"},
        ],
    }


def record(seat: int | None, **extra: Any) -> DecisionRecord:
    return DecisionRecord(
        turn=1,
        faction="F",
        engine="thinker",
        scope="base",
        tier="llm",
        world_view_hash="h",
        action_space_size=2,
        seat=seat,
        **extra,
    )


# --------------------------------------------------------------------------- the sink


def test_emit_with_a_stalled_viewer_neither_blocks_nor_raises() -> None:
    live = LiveStream(queue_size=4)
    stalled = live.subscribe(None)  # subscribed, never read
    emitter = Emitter(live)

    started = time.monotonic()
    for _ in range(1000):
        emitter.emit(record(3))
    assert time.monotonic() - started < 2.0
    assert emitter.healthy
    # Bounded: the backlog stays at its cap, the oldest are dropped, and every drop is counted.
    assert len(stalled._events) == 4
    assert stalled.dropped == 996
    assert live.dropped == 996
    # What survives is the newest, not the oldest.
    assert [seq for seq, _, _ in stalled._events] == [997, 998, 999, 1000]


def test_emit_with_no_viewer_at_all_is_a_no_op() -> None:
    live = LiveStream()
    emitter = Emitter(live)
    emitter.emit(record(None))
    assert emitter.healthy and live.published == 1 and live.viewers == 0


def test_the_stream_is_registered_after_the_record_of_truth(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    live = LiveStream()
    other: list[DecisionRecord] = []

    class Other:
        def write(self, rec: DecisionRecord) -> None:
            other.append(rec)

    sinks = sinks_for(log, extra=[Other()], live=live)
    assert sinks[0] is log and sinks[-1] is live


def test_a_viewer_hears_of_a_decision_only_after_the_jsonl_has_it(
    tmp_path: Path, thinker_base: WorldView
) -> None:
    log_path = tmp_path / "d.jsonl"
    live = LiveStream()
    sub = live.subscribe(None)
    seen_on_disk: list[int] = []

    original = sub.offer

    def offer(item: tuple[int, str, dict[str, Any]]) -> None:
        # At the moment the stream publishes, the line must already be in the log.
        seen_on_disk.append(len(log_path.read_text().splitlines()) if log_path.exists() else 0)
        original(item)

    sub.offer = offer  # type: ignore[method-assign]
    orch = Orchestrator(brain=ScriptedBrain(), log=DecisionLog(log_path), sinks=[live])
    orch.decide(thinker_base)
    orch.decide(thinker_base)

    lines = [json.loads(x) for x in log_path.read_text().splitlines()]
    assert len(lines) == 2
    assert seen_on_disk == [1, 2]
    assert [k for _, k, _ in sub._events] == ["answered", "answered"]


def test_a_seat_subscription_never_receives_another_factions_event() -> None:
    live = LiveStream()
    three, spectator = live.subscribe(3), live.subscribe(None)
    for seat in (3, 5, 5, 3, None):
        live.write(record(seat))
    live.outcome("00-unknown-trace", "applied")

    assert [p["faction_id"] for _, _, p in three._events] == [3, 3]
    assert [p["faction_id"] for _, _, p in spectator._events] == [3, 5, 5, 3, None, None]


def test_the_guard_verdict_is_its_own_event() -> None:
    live = LiveStream()
    sub = live.subscribe(None)
    rec = record(3)
    rec.knowledge.hank_verdict = "deny"
    rec.knowledge.stripped = ["base 9 garrison"]
    live.write(rec)
    kinds = [(k, p.get("verdict")) for _, k, p in sub._events]
    assert kinds == [("answered", None), ("guard", "deny")]


def test_now_shows_open_decisions_and_who_is_thinking() -> None:
    live = LiveStream(budgets={3: 5.0, 5: None})
    live.raised(WorldView.model_validate(view(3)))
    live.claimed(3, "base.production", "seat-3")
    snapshot = live.now(None)
    [row] = snapshot["open"]
    assert row["state"] == "claimed" and row["thinking"] == "seat-3"
    assert row["age_s"] >= 0 and row["thinking_s"] >= 0
    # A seat with a budget gets a meter; one with neither a budget nor any recorded spend
    # gets none, rather than an invented $0.
    assert snapshot["spend"] == [{"faction_id": 3, "spent_usd": None, "budget_usd": 5.0}]
    assert live.now(5)["open"] == []

    live.write(record(3, cost_usd=0.25))
    assert live.now(None)["open"] == []
    assert live.now(3)["spend"] == [{"faction_id": 3, "spent_usd": 0.25, "budget_usd": 5.0}]


# --------------------------------------------------------------------------- SSE framing


def test_sse_framing_round_trips() -> None:
    payload = {"kind": "raised", "text": "line one\nline two", "n": [1, 2], "u": "Ünïcode"}
    text = sse_frame("raised", payload, 7) + ": keepalive\n\n" + sse_frame("hello", {"a": 1})
    assert parse_sse(text) == [
        {"id": 7, "event": "raised", "data": payload},
        {"event": "hello", "data": {"a": 1}},
    ]
    # One JSON line per data field, so an embedded newline cannot end the frame early.
    assert sse_frame("x", payload).count("\n\n") == 1


def test_event_stream_says_hello_then_streams_and_unsubscribes() -> None:
    live = LiveStream()
    sub = live.subscribe(3)
    live.write(record(3))
    frames = parse_sse("".join(event_stream(live, sub, "player", heartbeat=0.01, limit=1)))
    assert [f["event"] for f in frames] == ["hello", "answered"]
    assert frames[0]["data"]["view"] == "player"
    assert live.viewers == 0


# --------------------------------------------------------------------------- the endpoint


@pytest.fixture
def seated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    monkeypatch.setenv("NA_RUN_DIR", str(tmp_path))
    monkeypatch.delenv("NA_SPECTATOR_TOKEN", raising=False)
    router = SeatRouter({3: Seat(3, "three", "scripted"), 5: Seat(5, "five", "scripted")})
    app = create_app(brain=router, log=DecisionLog(tmp_path / "orch" / "decisions.jsonl"))
    return app, TestClient(app), tmp_path


def test_no_token_is_refused_and_streams_nothing(seated) -> None:
    app, client, _ = seated
    for headers, url in (
        ({}, "/dashboard/api/events"),
        ({}, "/dashboard/api/events?token=not-a-token"),
        ({SEAT_HEADER: "forged"}, "/dashboard/api/events"),
    ):
        response = client.get(url, headers=headers)
        assert response.status_code == 401
        assert "data:" not in response.text
    assert app.state.live.viewers == 0
    assert app.state.run_record.observer_spoiled_at is None


def test_the_spectator_token_is_written_0600_like_a_seats(seated) -> None:
    app, _, run_dir = seated
    path = run_dir / "spectator" / "token"
    assert path.read_text() == app.state.viewers.spectator
    assert path.stat().st_mode & 0o777 == 0o600


def test_the_spectator_opening_sets_observer_spoiled_exactly_once(seated) -> None:
    app, client, run_dir = seated
    (run_dir / "run.json").write_text(json.dumps({"run_id": "r1"}))
    spectator = app.state.viewers.spectator
    seat3 = app.state.seat_tokens.tokens[3]

    # A player's own view does not spoil anything.
    player = client.get(f"/dashboard/api/events?token={seat3}&limit=0")
    assert parse_sse(player.text)[0]["data"]["view"] == "player"
    assert not (json.loads((run_dir / "run.json").read_text())).get("observer_spoiled")

    first = client.get(f"/dashboard/api/events?token={spectator}&limit=0")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert parse_sse(first.text)[0]["data"]["view"] == "spectator"
    written = json.loads((run_dir / "run.json").read_text())
    assert written["run_id"] == "r1"  # merged into the record, not replacing it
    assert written["observer_spoiled"] is True
    stamp = written["observer_spoiled_at"]

    time.sleep(1.1)
    client.get("/dashboard/api/events", params={"token": spectator, "limit": 0})
    assert json.loads((run_dir / "run.json").read_text())["observer_spoiled_at"] == stamp
    assert client.get("/health").json()["live"]["observer_spoiled_at"] == stamp


def test_run_record_spoils_once_even_without_a_run_dir() -> None:
    rr = RunRecord(None)
    assert rr.spoil() is True
    assert rr.spoil() is False


def test_a_seat_token_on_the_wire_never_carries_another_factions_event(seated) -> None:
    app, client, _ = seated
    seat3 = app.state.seat_tokens.tokens[3]
    got: dict[str, str] = {}

    def watch() -> None:
        got["text"] = client.get(
            "/dashboard/api/events", headers={SEAT_HEADER: seat3}, params={"limit": 6}
        ).text

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    deadline = time.monotonic() + 5
    while app.state.live.viewers == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app.state.live.viewers == 1

    for fid in (5, 3, 5, 3):
        assert client.post("/decide", json=view(fid)).status_code == 200
    watcher.join(timeout=5)
    assert not watcher.is_alive()

    frames = parse_sse(got["text"])
    events = [f for f in frames if f["event"] != "hello"]
    # Each decision is raised, answered, then carries the policy guard's verdict.
    assert [f["event"] for f in events] == ["raised", "answered", "guard"] * 2
    assert events[2]["data"]["verdict"] == "allow"
    assert {f["data"]["faction_id"] for f in events} == {3}
    assert "F5" not in got["text"] and '"faction_id":5' not in got["text"]
    # The raise and its answer are one decision, keyed the same.
    assert events[0]["data"]["decision"] == events[1]["data"]["decision"]


def test_the_page_carries_the_now_pane() -> None:
    page = TestClient(create_app(brain=ScriptedBrain())).get("/dashboard").text
    assert "<h2>NOW</h2>" in page
    assert "EventSource('/dashboard/api/events?token='" in page
    assert "startNow()" in page
