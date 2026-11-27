"""Seat tokens and seat-scoped agent calls (aegis-8fye.3, slice C).

Under a ``[seats]`` table the service mints one token per seat, and every agent-facing call is
scoped by the token rather than by a ``faction_id`` argument. The tests that matter are the
negative ones: seat 3 holding its own valid token must not be able to claim, answer, forecast or
order anything of seat 5's — and the queue must be exactly as it was after each attempt.
"""

from __future__ import annotations

import stat
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.config import Seat
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.doorbell import Doorbell
from neural_amplifier.pending import DecisionQueue, NotClaimable
from neural_amplifier.seats import SEAT_HEADER, SeatRouter
from neural_amplifier.service import create_app
from neural_amplifier.turns import ExpectedDecision, TurnAnnouncement


def world_view(faction_id: int, units: list[int], base_id: int) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.production",
        "turn": 43,
        "faction": f"F{faction_id}",
        "faction_id": faction_id,
        "base_id": base_id,
        "units": [{"id": u} for u in units],
        "bases": [{"name": f"B{base_id}", "base_id": base_id}],
        "action_space": [
            {"id": "unit:0", "action": "Colony Pod"},
            {"id": "facility:4", "action": "Recycling Tanks"},
        ],
    }


@pytest.fixture
def seated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A service seating agents at factions 3 and 5, sharing one muted queue."""
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    monkeypatch.setenv("NA_RUN_DIR", str(tmp_path))
    queue = DecisionQueue()
    router = SeatRouter(
        {3: Seat(3, "three", "agent"), 5: Seat(5, "five", "agent")},
        build=lambda seat: AgentBrain(
            queue=queue, doorbell=Doorbell(target="", enabled=False), timeout=10
        ),
    )
    app = create_app(brain=router)
    client = TestClient(app)
    tokens = app.state.seat_tokens.tokens
    return app, client, queue, tokens, tmp_path


def _as(token: str) -> dict[str, str]:
    return {SEAT_HEADER: token}


def _post(client: TestClient, body: dict[str, Any], results: dict) -> threading.Thread:
    def call() -> None:
        results["response"] = client.post("/decide", json=body)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    return thread


def _await_pending(queue: DecisionQueue, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiting = queue.waiting()
        if waiting:
            return waiting[0].id
        time.sleep(0.01)
    raise AssertionError("the decision never reached the queue")


def _snapshot(queue: DecisionQueue) -> list[tuple[str, str, str | None]]:
    return [(p.id, p.status, p.claimed_by) for p in queue.waiting()]


def test_each_seat_gets_its_own_token_file_at_0600(seated) -> None:
    _, _, _, tokens, run_dir = seated
    assert sorted(tokens) == [3, 5]
    assert tokens[3] != tokens[5]
    for fid, token in tokens.items():
        path = run_dir / f"seat-{fid}" / "token"
        assert path.read_text() == token
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert len(token) >= 43  # token_urlsafe(32)


@pytest.mark.parametrize(
    ("method", "route", "body"),
    [
        ("post", "/agent/next", {"wait": 0}),
        ("post", "/agent/submit", {"decision_id": "x", "action_id": "y"}),
        ("post", "/agent/turn", {"turn": 43}),
        ("post", "/agent/plan", {"turn": 43, "entries": []}),
        ("get", "/agent/plan", None),
        ("get", "/agent/pending", None),
        ("post", "/agent/waiting", None),
        ("post", "/order", {"verb": "skip", "args": [1]}),
    ],
)
@pytest.mark.parametrize("token", [None, "not-a-seat-token"])
def test_no_or_unknown_token_in_seat_mode_is_401(seated, method, route, body, token) -> None:
    _, client, _, _, _ = seated
    headers = _as(token) if token else {}
    if method == "get":
        response = client.get(route, headers=headers)
    else:
        response = client.post(route, json=body, headers=headers)
    assert response.status_code == 401, response.text


def test_seat_3_cannot_claim_or_answer_a_seat_5_decision(seated) -> None:
    _, client, queue, tokens, _ = seated
    results: dict = {}
    thread = _post(client, world_view(5, [501], 50), results)
    decision_id = _await_pending(queue)
    before = _snapshot(queue)

    # Claim: seat 3 is offered nothing, and the decision stays unclaimed.
    offered = client.post("/agent/next", json={"wait": 0}, headers=_as(tokens[3])).json()
    assert offered["decision_id"] is None
    assert _snapshot(queue) == before
    assert client.post("/agent/waiting", headers=_as(tokens[3])).json()["waiting"] == []

    # Seat 5 claims it; from here seat 3's attempts must leave the claim exactly as it is.
    claimed = client.post("/agent/next", json={"wait": 0}, headers=_as(tokens[5])).json()
    assert claimed["decision_id"] == decision_id
    before = _snapshot(queue)

    refused = client.post(
        "/agent/submit",
        json={"decision_id": decision_id, "action_id": "unit:0"},
        headers=_as(tokens[3]),
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    # Worded as an unknown id: it must not name the chair that owns the decision.
    assert "5" not in detail.replace(decision_id, "")
    assert "seat" not in detail.lower()
    assert _snapshot(queue) == before

    directive = client.post(
        "/agent/directive",
        json={
            "decision_id": decision_id,
            "id": "d",
            "intent": "x",
            "metric": "energy_reserves",
            "comparator": "hold",
        },
        headers=_as(tokens[3]),
    )
    assert directive.status_code == 409
    whatif = client.post(
        "/agent/whatif",
        json={"decision_id": decision_id, "action_id": "unit:0"},
        headers=_as(tokens[3]),
    )
    assert whatif.status_code == 404
    assert _snapshot(queue) == before

    # The rightful seat still answers, and the game gets its orders.
    answered = client.post(
        "/agent/submit",
        json={"decision_id": decision_id, "action_id": "facility:4"},
        headers=_as(tokens[5]),
    )
    assert answered.status_code == 200, answered.text
    thread.join(5)
    assert results["response"].json()["choices"][0]["action_id"] == "facility:4"


def test_seat_3_cannot_forecast_seat_5s_turn(seated) -> None:
    _, client, _, tokens, _ = seated
    client.post(
        "/turn",
        json=TurnAnnouncement(
            turn=43,
            expected=[
                ExpectedDecision(
                    surface_id="base.production", faction_id=3, base_id=30, base="Three Base"
                ),
                ExpectedDecision(
                    surface_id="base.production", faction_id=5, base_id=50, base="Five Base"
                ),
            ],
        ).model_dump(),
    )
    mine = client.post("/agent/turn", json={"turn": 43}, headers=_as(tokens[3]))
    assert mine.status_code == 200
    assert mine.json()["faction_id"] == 3
    assert {d["base"] for d in mine.json()["decisions"]} == {"Three Base"}

    # Naming the other seat's faction is refused, without confirming whose it is.
    named = client.post("/agent/turn", json={"turn": 43, "faction_id": 5}, headers=_as(tokens[3]))
    assert named.status_code == 403
    assert "Five Base" not in named.text


def test_seat_3_cannot_order_a_seat_5_entity(seated, monkeypatch: pytest.MonkeyPatch) -> None:
    app, client, queue, tokens, _ = seated
    issued: list[Any] = []
    channel = app.state.orders
    monkeypatch.setattr(
        channel, "issue", lambda command, timeout_s=None: issued.append(command) or _Outcome()
    )
    monkeypatch.setattr(
        channel, "issue_batch", lambda lines, timeout_s=None: issued.append(lines) or _Batch()
    )
    # Both seats have posted a world view; seat 5's is still pending when seat 3 orders.
    for fid, units, base in ((3, [301], 30), (5, [501], 50)):
        app.state.latest_view[fid] = WorldView.model_validate(world_view(fid, units, base))
    results: dict = {}
    thread = _post(client, world_view(5, [501], 50), results)
    decision_id = _await_pending(queue)
    before = _snapshot(queue)

    for body in (
        {"verb": "move", "args": [501, 1, 1]},
        {"verb": "skip", "args": [501]},
        {"verb": "build", "args": [50, 4]},
        {"command": "skip 501"},
        # A batch with one foreign entity issues NOTHING, not the half that was the seat's.
        {"orders": [{"verb": "skip", "args": [301]}, {"verb": "skip", "args": [501]}]},
    ):
        refused = client.post("/order", json=body, headers=_as(tokens[3]))
        assert refused.status_code == 403, body
        assert "seat 5" not in refused.text
        assert issued == []
        assert _snapshot(queue) == before

    ok = client.post("/order", json={"verb": "skip", "args": [301]}, headers=_as(tokens[3]))
    assert ok.status_code == 200
    assert issued == ["skip 301"]

    queue.answer(decision_id, Orders(choices=[Choice(action_id="unit:0")]))
    thread.join(5)


def test_a_seat_with_no_world_view_yet_can_order_nothing(seated) -> None:
    _, client, _, tokens, _ = seated
    refused = client.post("/order", json={"verb": "skip", "args": [301]}, headers=_as(tokens[3]))
    assert refused.status_code == 403


def test_the_queue_refuses_a_wrong_seat_without_naming_the_owner() -> None:
    queue = DecisionQueue()
    pending = queue.post(WorldView.model_validate(world_view(5, [], 50)))
    assert queue.claim(seat=3) is None
    assert pending.status == "pending"
    assert queue.claim(seat=5) is pending

    with pytest.raises(NotClaimable) as answered:
        queue.answer(pending.id, Orders(choices=[Choice(action_id="unit:0")]), seat=3)
    with pytest.raises(NotClaimable) as abandoned:
        queue.abandon(pending.id, "giving up", seat=3)
    for exc in (answered.value, abandoned.value):
        assert "5" not in str(exc).replace(pending.id, "")
    assert pending.status == "claimed"
    assert pending.orders is None

    queue.answer(pending.id, Orders(choices=[Choice(action_id="unit:0")]), seat=5)
    assert pending.status == "answered"


def test_legacy_mode_derives_the_one_llm_faction_that_posted(monkeypatch) -> None:
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    app = create_app(brain=brain)
    client = TestClient(app)

    before = client.post("/agent/turn", json={"turn": 43})
    assert before.status_code == 422
    assert "configure [seats]" in before.json()["detail"]

    results: dict = {}
    thread = _post(client, world_view(3, [301], 30), results)
    decision_id = _await_pending(brain.queue)
    view = client.post("/agent/turn", json={"turn": 43})
    assert view.status_code == 200
    assert view.json()["faction_id"] == 3
    plan = client.post("/agent/plan", json={"turn": 43, "entries": []})
    assert plan.status_code == 200
    assert plan.json()["plan"]["faction_id"] == 3

    claimed = client.post("/agent/next", json={"wait": 0}).json()
    assert claimed["decision_id"] == decision_id
    client.post("/agent/submit", json={"decision_id": decision_id, "action_id": "unit:0"})
    thread.join(5)


class _Outcome:
    def as_dict(self) -> dict[str, object]:
        return {"status": "ok"}


class _Batch:
    results: list[dict[str, object]] = []

    def as_dict(self) -> dict[str, object]:
        return {"status": "ok", "results": []}
