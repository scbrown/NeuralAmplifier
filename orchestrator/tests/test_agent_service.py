"""The agent playing through the real service.

`test_agent_brain.py` covers the queue. This covers the thing that actually has to work: a
``POST /decide`` blocked on a worker thread while an agent claims and answers over the
``/agent/*`` endpoints, with the whole decision loop — validation, guard, record — running
around it exactly as it does for a model.

The distinction worth keeping: an agent is not a privileged client. It sends orders the same
way a brain returns them, and everything downstream treats them identically.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.doorbell import Doorbell
from neural_amplifier.service import create_app

WORLD_VIEW = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "base",
    "surface_id": "base.production",
    "turn": 42,
    "faction": "Gaians",
    "base": "Gaia's Landing",
    "metrics": {"energy_reserves": 82},
    "action_space": [
        {"id": "unit:0", "action": "Colony Pod", "cost": 30},
        {"id": "facility:4", "action": "Recycling Tanks", "cost": 40},
    ],
}


@pytest.fixture
def agent_app(tmp_path):
    """A service whose brain is an attached agent, with the doorbell muted."""
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    app = create_app(brain=brain)
    return app, brain


def _post_decision(client: TestClient, results: dict) -> threading.Thread:
    """Run POST /decide on its own thread, because it is going to block."""

    def call() -> None:
        response = client.post("/decide", json=WORLD_VIEW)
        results["status"] = response.status_code
        results["body"] = response.json()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    return thread


def _await_claim(client: TestClient, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.post("/agent/next", json={"wait": 0}).json()
        if payload.get("decision_id"):
            return payload
        time.sleep(0.02)
    raise AssertionError("no decision was offered to the agent")


def test_an_agent_answers_a_blocked_decision(agent_app) -> None:
    """The end-to-end shape: the game waits, the agent decides, the game gets the orders."""
    app, _ = agent_app
    client = TestClient(app)
    results: dict = {}
    thread = _post_decision(client, results)

    claimed = _await_claim(client)
    assert claimed["surface_id"] == "base.production"
    # The agent receives a full world view, not a summary — it is standing exactly where the
    # brain stands, after grounding and directives have been injected.
    assert claimed["world_view"]["action_space"][0]["id"] == "unit:0"
    assert claimed["world_view"]["faction"] == "Gaians"

    submitted = client.post(
        "/agent/submit",
        json={
            "decision_id": claimed["decision_id"],
            "action_id": "facility:4",
            "reason": "infrastructure before expansion",
        },
    )
    assert submitted.status_code == 200
    # The submit result reports what *ran*, not what was asked for. Validation and the guard
    # sit between the two, and an agent told its stripped choice was applied has no reason to
    # repair it.
    answered = submitted.json()
    assert answered["submitted"] == "facility:4"
    assert answered["applied"] == ["facility:4"]
    assert answered["status"] == "applied to the game"
    assert answered["degraded"] is False

    thread.join(timeout=5)
    assert not thread.is_alive(), "POST /decide never returned"
    assert results["status"] == 200
    assert results["body"]["choices"][0]["action_id"] == "facility:4"


def test_an_invented_action_is_refused_with_the_legal_set(agent_app) -> None:
    """Invariant 1, delivered as a tool result.

    The orchestrator would strip an illegal choice anyway — but silently, and a model cannot
    correct a mistake nobody told it about. So the agent is refused *and* handed the legal ids.
    """
    app, _ = agent_app
    client = TestClient(app)
    results: dict = {}
    thread = _post_decision(client, results)
    claimed = _await_claim(client)

    refused = client.post(
        "/agent/submit",
        json={"decision_id": claimed["decision_id"], "action_id": "unit:999"},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert "unit:999" in detail
    assert "facility:4" in detail and "unit:0" in detail

    # And the decision is still answerable — a refusal must not consume it, or one bad guess
    # would cost the turn.
    good = client.post(
        "/agent/submit",
        json={"decision_id": claimed["decision_id"], "action_id": "unit:0"},
    )
    assert good.status_code == 200
    thread.join(timeout=5)
    assert results["body"]["choices"][0]["action_id"] == "unit:0"


def test_answering_twice_is_a_conflict(agent_app) -> None:
    app, _ = agent_app
    client = TestClient(app)
    results: dict = {}
    thread = _post_decision(client, results)
    claimed = _await_claim(client)

    first = client.post(
        "/agent/submit", json={"decision_id": claimed["decision_id"], "action_id": "unit:0"}
    )
    assert first.status_code == 200
    second = client.post(
        "/agent/submit", json={"decision_id": claimed["decision_id"], "action_id": "facility:4"}
    )
    assert second.status_code == 409
    assert "already answered" in second.json()["detail"]
    thread.join(timeout=5)


def test_waiting_lists_what_is_outstanding(agent_app) -> None:
    """What an agent calls after a reconnect, instead of guessing what it already did."""
    app, _ = agent_app
    client = TestClient(app)
    results: dict = {}
    thread = _post_decision(client, results)
    _await_claim(client)

    listed = client.post("/agent/waiting").json()["waiting"]
    assert len(listed) == 1
    assert listed[0]["surface_id"] == "base.production"
    assert listed[0]["status"] == "claimed"
    assert listed[0]["turn"] == 42

    client.post(
        "/agent/submit",
        json={"decision_id": listed[0]["decision_id"], "action_id": "unit:0"},
    )
    thread.join(timeout=5)
    assert client.post("/agent/waiting").json()["waiting"] == []


def test_no_decision_waiting_is_not_an_error(agent_app) -> None:
    """An agent that wakes with nothing to do must be able to tell that apart from a fault."""
    app, _ = agent_app
    client = TestClient(app)
    payload = client.post("/agent/next", json={"wait": 0}).json()
    assert payload["decision_id"] is None


def test_polling_alone_is_enough_to_play(agent_app) -> None:
    """The guaranteed path: no doorbell, no tmux, no configuration.

    A harness must be able to *ask* for open decisions and get them. The nudge is a
    convenience that can be absent or silently lost, so anything that only worked when it
    fired would be a single point of failure with no error reporting — `send-keys` cannot
    report one. This fixture has the doorbell disabled entirely, which is the point.
    """
    app, brain = agent_app
    assert brain.doorbell.enabled is False
    client = TestClient(app)
    results: dict = {}

    # The agent asks first and is told to wait — it has not been notified of anything.
    assert client.post("/agent/next", json={"wait": 0}).json()["decision_id"] is None

    thread = _post_decision(client, results)
    # A blocking ask: this returns when the game posts, without anything having told the
    # agent to look.
    claimed = client.post("/agent/next", json={"wait": 5}).json()
    assert claimed["decision_id"], "a blocking poll must return the decision that arrives"

    client.post(
        "/agent/submit",
        json={"decision_id": claimed["decision_id"], "action_id": "unit:0"},
    )
    thread.join(timeout=5)
    assert results["body"]["choices"][0]["action_id"] == "unit:0"


def test_agent_endpoints_are_absent_for_a_scripted_run() -> None:
    """Mounting them unconditionally would advertise a queue nothing fills, and an agent
    connecting to a scripted service would wait forever with no way to tell why."""
    client = TestClient(create_app())
    assert client.post("/agent/next", json={}).status_code == 404


def test_health_names_the_agent_brain(agent_app) -> None:
    app, _ = agent_app
    assert TestClient(app).get("/health").json()["brain"] == "agent"


def test_the_decision_record_is_written_for_an_agent_answer(agent_app, tmp_path) -> None:
    """An agent-driven decision is a decision: same record, same log, same coverage.

    If this were not true, every measurement the project has — degrade rate, adherence,
    fair-play — would quietly stop covering the way the game is actually played.
    """
    from neural_amplifier.decisions import DecisionLog

    log_path = tmp_path / "decisions.jsonl"
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    client = TestClient(create_app(brain=brain, log=DecisionLog(log_path)))
    results: dict = {}
    thread = _post_decision(client, results)
    claimed = _await_claim(client)
    client.post(
        "/agent/submit",
        json={
            "decision_id": claimed["decision_id"],
            "action_id": "unit:0",
            "reason": "expand while land is free",
        },
    )
    thread.join(timeout=5)

    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    record = records[0]
    assert record["surface_id"] == "base.production"
    assert record["chosen"][0]["action_id"] == "unit:0"
    assert record["reason"] == "expand while land is free"
    # `degraded` is the single most valuable field in a record: a run of pure fallbacks
    # otherwise completes and looks green. An answered agent decision is not a fallback.
    assert record["degraded"] is False
    assert record["adherence_violations"] == 0


def test_a_denied_order_becomes_a_repair_the_agent_can_answer() -> None:
    """The full deny-repair loop through the agent surface (na-7zl).

    `hurry:now` declares it spends 81 energy_reserves and only 40 are reported, so it is legal
    by the engine's action space and unpayable against current state. Before the repair loop
    that cost the whole decision. Now the agent is told what happened, handed the same decision
    again with the reason attached, and gets to choose something that works.

    The `degraded is False` at the end is the point of the whole exercise: a turn that used to
    fall back to the deterministic tier is now played by the brain.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    client = TestClient(create_app(brain=brain))
    results: dict = {}
    unaffordable = {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.hurry",
        "turn": 42,
        "faction": "Gaians",
        "metrics": {"energy_reserves": 40},
        "action_space": [
            {"id": "hurry:none", "action": "Do not hurry"},
            {"id": "hurry:now", "action": "Hurry production", "effects": {"energy_reserves": -81}},
        ],
    }

    def call() -> None:
        results["body"] = client.post("/decide", json=unaffordable).json()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    first = _await_claim(client)

    answered = client.post(
        "/agent/submit",
        json={"decision_id": first["decision_id"], "action_id": "hurry:now"},
    ).json()
    assert answered["submitted"] == "hurry:now"
    assert answered["applied"] == []
    assert "repair decision follows" in answered["status"]
    # It must say *why* here, not only on the repair — an agent that has to go and fetch the
    # reason separately is one that may not.
    assert any("only 40" in a for a in answered["advisories"])

    # The repair arrives as an ordinary decision, carrying the reason in `advisories`.
    second = _await_claim(client)
    assert second["decision_id"] != first["decision_id"]
    advisories = second["world_view"].get("advisories") or []
    assert any("only 40" in a for a in advisories), "the repair must carry what went wrong"

    fixed = client.post(
        "/agent/submit",
        json={
            "decision_id": second["decision_id"],
            "action_id": "hurry:none",
            "reason": "cannot afford it after all",
        },
    ).json()
    assert fixed["applied"] == ["hurry:none"]
    assert fixed["status"] == "applied to the game"

    thread.join(timeout=5)
    assert results["body"]["choices"][0]["action_id"] == "hurry:none"
    assert results["body"]["degraded"] is False, "a repaired decision is not a degraded one"


def test_an_unrepairable_denial_still_degrades_and_says_so() -> None:
    """The bound holds through the agent path too: one repair, then the fallback.

    An agent that keeps insisting on the same unpayable order must not be able to hold the turn
    open indefinitely.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    client = TestClient(create_app(brain=brain))
    results: dict = {}
    unaffordable = {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.hurry",
        "turn": 42,
        "faction": "Gaians",
        "metrics": {"energy_reserves": 40},
        "action_space": [
            {"id": "hurry:none", "action": "Do not hurry"},
            {"id": "hurry:now", "action": "Hurry production", "effects": {"energy_reserves": -81}},
        ],
    }

    def call() -> None:
        results["body"] = client.post("/decide", json=unaffordable).json()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()

    for _ in range(2):
        claimed = _await_claim(client)
        client.post(
            "/agent/submit",
            json={"decision_id": claimed["decision_id"], "action_id": "hurry:now"},
        )

    thread.join(timeout=5)
    assert results["body"]["degraded"] is True
    assert results["body"]["choices"][0]["action_id"] == "hurry:none", "the fallback still runs"


def test_an_agent_can_issue_a_plan_that_steers_a_later_decision(tmp_path) -> None:
    """na-43h, on the agent path: a long-horizon decision leaves something behind.

    `Orders.directives` has existed all along and no decision had ever issued one — every
    directive measured so far was hand-written into a plan file. An agent is the natural issuer,
    and until now the MCP surface gave it no way to.

    The assertion that matters is the second half: the plan is not just stored, it reaches the
    *next* decision as a `DirectiveStatus` with its current value attached.
    """
    from neural_amplifier.directives import DirectiveStore

    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    app = create_app(brain=brain)
    app.state.orchestrator.plan = DirectiveStore(tmp_path / "plan.json")
    client = TestClient(app)

    tech = {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "turn",
        "surface_id": "faction.tech",
        "turn": 40,
        "faction": "Gaians",
        "metrics": {"energy_reserves": 120, "energy_income": 14},
        "action_space": [{"id": "tech:5", "action": "Centauri Ecology"}],
    }
    results: dict = {}

    def call_tech() -> None:
        results["tech"] = client.post("/decide", json=tech).json()

    thread = threading.Thread(target=call_tech, daemon=True)
    thread.start()
    claimed = _await_claim(client)

    issued = client.post(
        "/agent/directive",
        json={
            "decision_id": claimed["decision_id"],
            "id": "fund-weather-paradigm",
            "intent": "save energy for the Weather Paradigm",
            "metric": "energy_reserves",
            "comparator": "at_least",
            "target": 300,
            "priority": 7,
            "entities": ["fac:the-weather-paradigm"],
        },
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["issued"] == "fund-weather-paradigm"

    client.post(
        "/agent/submit",
        json={"decision_id": claimed["decision_id"], "action_id": "tech:5", "reason": "eco path"},
    )
    thread.join(timeout=5)

    # The next decision, on a different surface, must be shown the plan.
    hurry = {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.hurry",
        "turn": 41,
        "faction": "Gaians",
        "metrics": {"energy_reserves": 120, "energy_income": 14},
        "action_space": [{"id": "hurry:none", "action": "Do not hurry"}],
    }

    def call_hurry() -> None:
        results["hurry"] = client.post("/decide", json=hurry).json()

    thread2 = threading.Thread(target=call_hurry, daemon=True)
    thread2.start()
    second = _await_claim(client)

    shown = second["world_view"].get("directives") or []
    assert shown, "a plan that does not reach the next decision has changed nothing"
    status = shown[0]
    assert status["directive"]["id"] == "fund-weather-paradigm"
    assert status["directive"]["priority"] == 7
    # With its current value attached — a directive without one is an instruction the model has
    # to guess the relevance of.
    assert status["current"] == 120
    assert status["satisfied"] is False, "120 is short of the 300 it asks for"

    client.post(
        "/agent/submit",
        json={
            "decision_id": second["decision_id"],
            "action_id": "hurry:none",
            "followed": ["fund-weather-paradigm"],
        },
    )
    thread2.join(timeout=5)


def test_a_directive_naming_an_unknown_metric_is_refused_while_the_agent_can_fix_it(
    agent_app,
) -> None:
    """Validation at issue time is the whole discipline.

    Accepting this and discovering on every later turn that it cannot be evaluated would read
    in a record as compliance rather than as a gap.
    """
    app, _ = agent_app
    client = TestClient(app)
    results: dict = {}
    thread = _post_decision(client, results)
    claimed = _await_claim(client)

    refused = client.post(
        "/agent/directive",
        json={
            "decision_id": claimed["decision_id"],
            "id": "be-aggressive",
            "intent": "play aggressively",
            "metric": "aggression",
            "comparator": "at_least",
            "target": 5,
        },
    )
    assert refused.status_code == 422
    assert "aggression" in refused.json()["detail"]

    # The decision is untouched: a bad plan must not cost the choice it arrived with.
    good = client.post(
        "/agent/submit", json={"decision_id": claimed["decision_id"], "action_id": "unit:0"}
    )
    assert good.status_code == 200
    thread.join(timeout=5)
    assert results["body"]["choices"][0]["action_id"] == "unit:0"
