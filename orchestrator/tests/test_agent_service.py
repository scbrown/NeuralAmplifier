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
    # It says what this process did, not what happened on the board. The orchestrator does not
    # observe the game — an HTTP response, the adapter's legality gates and the engine itself
    # sit between here and anything being built — and the string it used to return claimed
    # otherwise on every decision, including the ones the game had already abandoned (na-t3h).
    assert answered["status"] == "accepted — returned to the engine to apply"
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
    assert fixed["status"] == "accepted — returned to the engine to apply"

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


# ------------------------------------------------------- the engine's own deadline (na-t3h)
#
# The adapter blocks for `conf.llm_timeout_ms` and then applies the deterministic tier's pick
# and moves on. The orchestrator's wait defaulted to *forever*. Nothing connected the two, so a
# late answer completed a decision loop for a turn the game had already resolved and the record
# said `tier=llm, degraded=false` while `/agent/submit` replied "applied to the game" — against
# 66 adapter rows for the same run, every one `applied=native`. Both logs well-formed, flatly
# disagreeing about who decided the game.
#
# `decision_deadline_ms` is the adapter saying how long it will still be listening. These tests
# are the mechanism, not the intention: they assert the orchestrator gives up FIRST.

#: Short enough that the tests do not sleep, long enough that the shave in
#: ``ABANDON_MARGIN_SECONDS`` cannot drive the wait to zero and make them pass for the wrong
#: reason — a decision abandoned instantly would satisfy every assertion below without the
#: deadline arithmetic ever having been exercised.
DEADLINE_MS = 400

DEADLINED = dict(WORLD_VIEW, decision_deadline_ms=DEADLINE_MS)


def test_a_decision_the_engine_stopped_waiting_for_is_recorded_as_degraded(tmp_path) -> None:
    """The whole of na-t3h, as one record.

    A run where the game abandoned every decision and the agent answered every one of them late
    finishes clean and reads as a fully brain-driven game — `degraded` is what makes that class
    of failure loud (observability.md §5.4), and it was reading False on decisions the game
    never used. The brain must wait on the *engine's* clock, not only its own, or the field
    measures the orchestrator's opinion of itself.

    `timeout=None` here is the shipped default and is the exact configuration that failed: this
    brain would otherwise wait forever for an answer no one is listening for any more.
    """
    from neural_amplifier.decisions import DecisionLog

    log_path = tmp_path / "decisions.jsonl"
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=None)
    client = TestClient(create_app(brain=brain, log=DecisionLog(log_path)))
    results: dict = {}

    def call() -> None:
        results["body"] = client.post("/decide", json=DEADLINED).json()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    _await_claim(client)  # claimed, and then deliberately never answered

    thread.join(timeout=5)
    assert not thread.is_alive(), "the game must not still be blocked past its own deadline"
    assert results["body"]["degraded"] is True

    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    record = records[0]
    assert record["degraded"] is True
    # And it must say the *game* stopped waiting. "no answer within 0.15s" is true and useless:
    # it reads as an orchestrator timeout somebody set too low, which is a different bug with a
    # different fix.
    assert f"{DEADLINE_MS}ms deadline" in record["degrade_reason"]
    assert "moved on" in record["degrade_reason"]


def test_an_answer_after_the_engine_deadline_is_refused_rather_than_applied(tmp_path) -> None:
    """The second half, and the one that stops the false record being written at all.

    Refusing late is not tidiness. An agent that is told "applied to the game" builds its next
    turn's reasoning on a board state that never existed, and it has no way to discover the
    mistake — the orchestrator's own log agrees with it. A 409 naming the deadline is the only
    thing here that can reach the model.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=None)
    client = TestClient(create_app(brain=brain))
    results: dict = {}

    def call() -> None:
        results["body"] = client.post("/decide", json=DEADLINED).json()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    claimed = _await_claim(client)
    thread.join(timeout=5)
    assert results["body"]["degraded"] is True, "the deadline must have expired first"

    late = client.post(
        "/agent/submit",
        json={"decision_id": claimed["decision_id"], "action_id": "unit:0"},
    )
    assert late.status_code == 409
    detail = late.json()["detail"]
    assert claimed["decision_id"] in detail
    # The message has to say the game moved on and that resubmitting is not the repair, because
    # "abandoned" alone reads to a model as something it should try again.
    assert "moved on" in detail
    assert "Do not resubmit" in detail
    assert f"{DEADLINE_MS}ms deadline" in detail


def test_an_answer_inside_the_engine_deadline_is_unchanged(tmp_path) -> None:
    """The regression guard. The fix is worthless if it costs the decisions that were working.

    A generous deadline must behave exactly as no deadline did: answered, applied, not degraded,
    one ordinary record. This is deliberately the same shape as
    `test_the_decision_record_is_written_for_an_agent_answer` — if bounding the wait changed
    anything on the healthy path, these two assertions diverge.
    """
    from neural_amplifier.decisions import DecisionLog

    log_path = tmp_path / "decisions.jsonl"
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    client = TestClient(create_app(brain=brain, log=DecisionLog(log_path)))
    results: dict = {}
    generous = dict(WORLD_VIEW, decision_deadline_ms=30_000)

    def call() -> None:
        results["body"] = client.post("/decide", json=generous).json()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    claimed = _await_claim(client)

    answered = client.post(
        "/agent/submit",
        json={
            "decision_id": claimed["decision_id"],
            "action_id": "facility:4",
            "reason": "infrastructure before expansion",
        },
    )
    assert answered.status_code == 200
    assert answered.json()["applied"] == ["facility:4"]
    assert answered.json()["status"] == "accepted — returned to the engine to apply"
    assert answered.json()["degraded"] is False

    thread.join(timeout=5)
    assert results["body"]["choices"][0]["action_id"] == "facility:4"
    assert results["body"]["degraded"] is False

    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["degraded"] is False
    assert records[0]["degrade_reason"] is None


def test_an_adapter_that_states_no_deadline_still_gets_the_configured_wait() -> None:
    """Absent must not become a bound. Every adapter is absent until it is upgraded.

    The failure mode this rules out is the mirror of na-t3h and would be worse, because it would
    hit adapters that are behaving correctly: inventing a default deadline here would abandon
    decisions the game was still sitting and waiting for, converting a working run into a run of
    fallbacks with nothing in the log that looks like a change.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    client = TestClient(create_app(brain=brain))
    results: dict = {}
    thread = _post_decision(client, results)  # WORLD_VIEW carries no decision_deadline_ms

    claimed = _await_claim(client)
    assert "decision_deadline_ms" not in claimed["world_view"]
    # Well past any deadline the field could have implied, and the decision is still open.
    time.sleep(0.5)
    assert client.post("/agent/waiting").json()["waiting"], "the decision was abandoned early"

    client.post(
        "/agent/submit",
        json={"decision_id": claimed["decision_id"], "action_id": "unit:0"},
    )
    thread.join(timeout=5)
    assert results["body"]["choices"][0]["action_id"] == "unit:0"
    assert results["body"]["degraded"] is False


# ------------------------------------------- the game process that is gone (na-bzd)
#
# The deadline above is the case where the engine is ALIVE and has stopped waiting. This is the
# case where the engine no longer EXISTS, and no deadline reaches it: nothing on the orchestrator
# side counts down once a decision loop is already blocked, so the adapter's clock expires in a
# process that is not running to expire it.
#
# Measured 2026-08-02, and the acceptance below is that measurement turned into a test. The game
# was killed mid-decision and relaunched; the still-running orchestrator's `/agent/waiting`
# offered four decisions at turn 40, status "pending", ages 600-1275s, every one raised by a
# process dead for twenty minutes. Claiming and answering one returned the ordinary success
# response, `degraded: false`. They were indistinguishable from live work in the queue, in
# `/agent/waiting`, and to an agent polling `/agent/next`.
#
# A real game cannot be killed from a test, so the two runs are two `run_id`s posted through the
# TestClient. That substitution is honest for exactly the reason process identity was chosen over
# the alternatives: the orchestrator never observes the process, only the id it sends. A killed
# game and a second `run_id` are the same event as far as every line of code under test.

RUN_A = dict(WORLD_VIEW, run_id="run-a-4e1c8")
RUN_B = dict(WORLD_VIEW, turn=43, run_id="run-b-91f30")


def _post(client: TestClient, world_view: dict, results: dict, key: str) -> threading.Thread:
    """`POST /decide` for a named run, on its own thread, because it is going to block."""

    def call() -> None:
        results[key] = client.post("/decide", json=world_view).json()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    return thread


def test_a_killed_games_decisions_are_not_offered_to_the_agent_once_a_new_run_starts() -> None:
    """The acceptance, first half: kill a game mid-decision, start a new one, and confirm
    `/agent/waiting` does not offer the dead run's decisions.

    Both surfaces are asserted because both lied, independently. `/agent/waiting` is what an
    agent reads to answer "is the game waiting on me?", and `/agent/next` is what it reads to
    get work — an entry removed from one and still handed out by the other would leave the
    queue exactly as untrustworthy as it was, with a passing test.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=None)
    client = TestClient(create_app(brain=brain))
    results: dict = {}

    dead = _post(client, RUN_A, results, "a")
    orphan = _await_claim(client)
    assert orphan["world_view"]["turn"] == 42

    # The game is killed here. Nothing tells the orchestrator; the next thing it hears is the
    # relaunched process introducing itself.
    live = _post(client, RUN_B, results, "b")

    dead.join(timeout=5)
    assert not dead.is_alive(), "the worker blocked on the dead process was never released"

    listed = client.post("/agent/waiting").json()["waiting"]
    assert [entry["decision_id"] for entry in listed] != [orphan["decision_id"]]
    assert orphan["decision_id"] not in {entry["decision_id"] for entry in listed}
    assert [entry["turn"] for entry in listed] == [43], "only the live run may be offered"

    # And `/agent/next` hands out the new run's decision, not the dead one it would previously
    # have offered first — the queue is ordered oldest-first, so this is the ordering that
    # made the stale entries so easy to pick up.
    claimed = client.post("/agent/next", json={"wait": 5}).json()
    assert claimed["world_view"]["turn"] == 43

    client.post(
        "/agent/submit",
        json={"decision_id": claimed["decision_id"], "action_id": "unit:0"},
    )
    live.join(timeout=5)
    assert results["b"]["choices"][0]["action_id"] == "unit:0"


def test_answering_a_dead_runs_decision_is_refused_and_says_the_game_is_gone() -> None:
    """The acceptance, second half: submitting to one is refused rather than recorded.

    The refusal has to carry *why*. An agent that reads a bare 409 tries again, which is the
    one move guaranteed to be wasted here — there is no process left to try against. It also
    needs to learn that its board state is stale, and "abandoned" does not say that; only
    naming the run change does.

    The cost this prevents is real reasoning spent on a decision that cannot land, and a
    decision record written as though it did.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=None)
    client = TestClient(create_app(brain=brain))
    results: dict = {}

    dead = _post(client, RUN_A, results, "a")
    orphan = _await_claim(client)
    _post(client, RUN_B, results, "b")
    dead.join(timeout=5)

    refused = client.post(
        "/agent/submit",
        json={"decision_id": orphan["decision_id"], "action_id": "unit:0"},
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert orphan["decision_id"] in detail
    assert "game process that raised it is gone" in detail
    assert "run-a-4e1c8" in detail and "run-b-91f30" in detail
    assert "Do not resubmit" in detail


def test_the_record_for_a_dead_runs_decision_degrades_and_names_the_restart(tmp_path) -> None:
    """The other half of "refused rather than recorded", and the half na-t3h is about.

    Releasing the blocked worker writes a record. If that record read `degraded: false` the fix
    would have moved the false claim rather than removed it — a decision the brain never
    answered, for a game that no longer existed, logged as an ordinary one. Every number derived
    from the decision log rests on `degraded` meaning what it says.

    The reason matters as much as the flag. `degrade_reason` is where a run's post-mortem starts,
    and "no answer within Ns" would blame the agent for a game that was killed.
    """
    from neural_amplifier.decisions import DecisionLog

    log_path = tmp_path / "decisions.jsonl"
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=None)
    client = TestClient(create_app(brain=brain, log=DecisionLog(log_path)))
    results: dict = {}

    dead = _post(client, RUN_A, results, "a")
    _await_claim(client)
    _post(client, RUN_B, results, "b")
    dead.join(timeout=5)

    assert results["a"]["degraded"] is True
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["degraded"] is True
    assert "game process that raised it is gone" in records[0]["degrade_reason"]


def test_an_adapter_that_sends_no_run_id_keeps_every_decision_it_raised() -> None:
    """Absent means *cannot tell*, and cannot-tell must not be destructive.

    Every adapter is absent here until it is upgraded, and this is the failure that would hit
    the ones behaving correctly: if a missing run id read as "not the current run", each
    decision would retire the one before it. A game that never restarted would watch its own
    queue empty, degrade every turn, and blame a process death that never happened.

    Two decisions of the *same* real game are outstanding at once here, which is ordinary — the
    engine drills into several bases per turn.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=None)
    client = TestClient(create_app(brain=brain))
    results: dict = {}

    first = _post(client, WORLD_VIEW, results, "first")
    _await_claim(client)
    second = _post(client, dict(WORLD_VIEW, turn=43), results, "second")

    # Both still outstanding, and the first is still answerable — nothing may have been retired.
    deadline = time.monotonic() + 5
    while len(client.post("/agent/waiting").json()["waiting"]) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    listed = client.post("/agent/waiting").json()["waiting"]
    assert len(listed) == 2, "a silent adapter's decisions must not retire each other"

    for entry in listed:
        answered = client.post(
            "/agent/submit",
            json={"decision_id": entry["decision_id"], "action_id": "unit:0"},
        )
        assert answered.status_code == 200
    first.join(timeout=5)
    second.join(timeout=5)
    assert results["first"]["degraded"] is False
    assert results["second"]["degraded"] is False


def test_the_first_run_id_after_an_orchestrator_start_retires_nothing() -> None:
    """An orchestrator that has been serving an older adapter and then meets an upgraded one has
    no evidence of a restart — the decision it is holding may well belong to that same process.

    Adopting the id without acting on it is the only reading that cannot destroy live work. The
    alternative fails in the direction that is hardest to see: a decision the game is sitting and
    waiting for, abandoned by the orchestrator, on the very first turn after an upgrade.
    """
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=None)
    client = TestClient(create_app(brain=brain))
    results: dict = {}

    silent = _post(client, WORLD_VIEW, results, "silent")
    older = _await_claim(client)
    _post(client, RUN_A, results, "upgraded")

    deadline = time.monotonic() + 5
    while len(client.post("/agent/waiting").json()["waiting"]) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    listed = client.post("/agent/waiting").json()["waiting"]
    assert len(listed) == 2, "the first run id seen must adopt, not supersede"

    answered = client.post(
        "/agent/submit",
        json={"decision_id": older["decision_id"], "action_id": "unit:0"},
    )
    assert answered.status_code == 200, "the pre-upgrade decision was still live"
    silent.join(timeout=5)
    assert results["silent"]["degraded"] is False


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
