"""The MCP surface an agent plays through.

Thin by design — the tools are a façade over ``/agent/*`` — but the façade is the part a model
reads, so what it *says* is load-bearing. A tool whose description does not mention that only
ids from the action space are legal produces a model that guesses, and the refusal it then gets
is a round trip that need not have happened.

Driven against a fake client rather than a live service: the wiring to a real orchestrator is
covered in ``test_agent_service.py``, and what is worth isolating here is the translation —
including that an orchestrator's rejection arrives as text the model can act on rather than a
traceback it cannot.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from neural_amplifier.mcp_server import AgentError, OrchestratorClient, build_server


class FakeClient(OrchestratorClient):
    """An OrchestratorClient that answers from memory."""

    def __init__(self, decision: dict[str, Any] | None = None) -> None:
        super().__init__("http://fake")
        self.decision = decision
        self.submitted: list[tuple[str, str, str | None]] = []
        self.extras: list[dict[str, Any]] = []
        self.directives: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.turn_reads: list[tuple[int, int | None]] = []
        self.plan_reads: list[int] = []
        self.pending_reads: list[tuple[int, bool]] = []
        self.raises: Exception | None = None

    def order(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.raises:
            raise self.raises
        self.orders.append(payload)
        return {"status": "ok", "command": "skip 3", "detail": "veh 3 ends its turn"}

    def outcomes(self, cursor: int) -> dict[str, Any]:
        if self.raises:
            raise self.raises
        return {"cursor": cursor, "outcomes": [], "stats": {"decisions": 0}}

    def next_decision(self, wait: float) -> dict[str, Any]:
        if self.raises:
            raise self.raises
        return self.decision or {"decision_id": None, "waiting": 0}

    def submit(
        self, decision_id: str, action_id: str, reason: str | None, **extra: Any
    ) -> dict[str, Any]:
        if self.raises:
            raise self.raises
        self.submitted.append((decision_id, action_id, reason))
        self.extras.append(extra)
        return {
            "decision_id": decision_id,
            "applied": [action_id],
            # Mirrors what the real service returns. A stub that keeps the retired
            # "applied to the game" wording would go on teaching the claim na-t3h removed.
            "status": "accepted — returned to the engine to apply",
        }

    def directive(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.raises:
            raise self.raises
        self.directives.append(payload)
        return {
            "decision_id": payload["decision_id"],
            "issued": payload["id"],
            "status": "attached",
        }

    def waiting(self) -> dict[str, Any]:
        return {"waiting": []}

    def turn(self, faction_id: int, turn: int | None = None) -> dict[str, Any]:
        self.turn_reads.append((faction_id, turn))
        return {"turn": turn or 103, "faction_id": faction_id, "decisions": [], "unraised": []}

    def plan_status(self, faction_id: int) -> dict[str, Any]:
        self.plan_reads.append(faction_id)
        return {"plans": [], "count": 0, "missed": []}

    def pending(self, faction_id: int, full: bool = False) -> dict[str, Any]:
        self.pending_reads.append((faction_id, full))
        return {"pending": [], "count": 0}


DECISION = {
    "decision_id": "base.production-1",
    "surface_id": "base.production",
    "world_view": {
        "engine": "thinker",
        "turn": 42,
        "action_space": [{"id": "unit:0", "action": "Colony Pod"}],
    },
}


def tools(server: Any) -> dict[str, Any]:
    listed = asyncio.run(server.list_tools())
    return {t.name: t for t in listed}


def call(server: Any, name: str, **kwargs: Any) -> str:
    """Invoke a tool and return the text a model would read.

    The SDK has returned a bare content list, a ``(content, structured)`` tuple and a
    ``CallToolResult`` across versions, so the text is dug out rather than indexed. Worth the
    few lines: the assertion these tests make is about the *words* the model receives, and a
    shape change that silently yielded an empty string would leave every one of them passing.
    """
    result = asyncio.run(server.call_tool(name, kwargs))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    text = "".join(getattr(block, "text", "") for block in content)
    assert text, f"{name} returned no text for a model to read"
    if getattr(result, "is_error", False):
        raise AgentError(text)
    return text


def test_the_surface_is_answering_plus_acting_and_nothing_that_reads_the_board() -> None:
    """Find out, read, answer, plan — plus act, and learn what the act did.

    This test used to assert exactly four tools, on the grounds that anything more "invites the
    model to go looking for game state instead of reading the world view it was handed". That
    rationale is right and it is NOT a count: the invariant is **no second source of board
    state**, and `issue_order` / `order_outcomes` do not offer one.

    `issue_order` acts rather than reads — a different axis entirely, and the one that lets an
    agent chain a dependent move instead of taking whatever the engine offers next.
    `order_outcomes` reports what the ENGINE did with an order afterwards, which is information
    the world view structurally cannot carry because it did not exist when the world view was
    built.

    So the guard below is the one that actually protects the principle: no tool may offer to read
    the board. Keeping the count instead would have blocked a safe addition while permitting an
    unsafe one with a familiar name.

    `what_if` is the addition that tested that reasoning, and it only qualifies because of a
    property that had to be BUILT rather than assumed. It speculates over a board composed
    entirely from the decision's own world view, so it cannot report an entity the agent was not
    already handed — it derives from the one source rather than adding a second. That closure was
    false when it was first written: yupana's ingest merges, so a base from an earlier decision
    survived into this one and `what_if` would have surfaced it. It is true now because the
    ingest is sent with `replace`, which makes the world view the whole of the board (yupana
    0.6.1). Take that away and this tool becomes exactly the thing this test forbids.
    """
    named = tools(build_server(FakeClient()))
    assert set(named) == {
        "next_decision",
        "submit_orders",
        "decisions_waiting",
        "issue_directive",
        "issue_order",
        "issue_orders",
        "order_outcomes",
        "what_if",
        # Answers, in bulk and in advance — the same axis as submit_orders, once per turn
        # instead of once per wake-up (na-7bk). It writes the plan table and reads nothing.
        "submit_turn_plan",
        # The three reads bulk-turn mode needs, and the reason they do not breach the invariant
        # below: each reports the ORCHESTRATOR'S record of its own decisions — forecast,
        # answered-from-a-table, parked — and none of them reports the board. Nothing reachable
        # through them was not already sent by the adapter for these very decisions.
        #
        # They were not optional politeness. `submit_turn_plan` shipped telling the model to
        # "read the turn forecast first (POST /agent/turn)", which an MCP client cannot do: it
        # holds tools, not HTTP verbs. So bulk-turn mode's first step was unreachable from the
        # only surface the attached agent has, and the instruction to perform it was printed in
        # the tool that needed it.
        "turn_forecast",
        "turn_plan_status",
        "deferred_decisions",
    }


def test_no_tool_offers_to_read_game_state() -> None:
    """The invariant behind the surface, asserted directly rather than via a count.

    A tool that hands back the board — tiles, units, a map, another faction's bases — would let a
    model reason from something the orchestrator never fog-gated and never recorded, and every
    measurement here assumes the world view is what the brain saw.
    """
    forbidden = ("read_map", "get_board", "list_units", "inspect_base", "map_tiles", "scan")
    named = tools(build_server(FakeClient()))
    assert not (set(named) & set(forbidden))


def test_the_turn_reads_are_faction_scoped_at_the_tool_signature() -> None:
    """Fog is a REQUIRED argument on every bulk-turn read, not a filter the model may omit.

    The store behind these holds all six factions' decisions in one keyspace — base ids are a
    single engine-wide sequence — and each slot carries its base's NAME. So an unscoped read is
    the adapter's own "information cheat" arriving by a second door. Measured on a live
    orchestrator before this was closed: a read made for one faction returned 49 of another
    faction's base names.

    Asserted on the tool SCHEMA rather than on behaviour, because behaviour cannot catch the
    failure that matters. An optional `faction_id` would work perfectly in every test that
    passes one; the leak is the call that omits it, and a model omits an optional argument for
    the same reason anyone does — nothing said it was load-bearing.
    """
    named = tools(build_server(FakeClient()))
    for tool in ("turn_forecast", "turn_plan_status", "deferred_decisions"):
        # The SDK spells this `input_schema` now and `inputSchema` before 2.0; the same version
        # drift `call()` above absorbs. Reading whichever exists keeps the assertion about the
        # schema rather than about the SDK release.
        entry = named[tool]
        schema = getattr(entry, "input_schema", None) or getattr(entry, "inputSchema", None)
        assert schema, f"{tool}: no input schema to check"
        assert "faction_id" in schema.get("properties", {}), tool
        assert "faction_id" in schema.get("required", []), f"{tool}: faction_id must be required"


def test_tool_descriptions_state_the_legality_rule() -> None:
    """Invariant 1 has to be visible to the model, not just enforced behind it.

    A model that does not know the action space is closed will invent plausible ids, and every
    one of those is a refused round trip. Saying so in the description is cheaper than the
    repair loop.
    """
    described = tools(build_server(FakeClient()))
    submit = (described["submit_orders"].description or "").lower()
    assert "action_space" in submit
    assert "rejected" in submit or "legal" in submit
    collect = (described["next_decision"].description or "").lower()
    assert "submit_orders" in collect, "the tool must say what to do next"


def test_next_decision_returns_the_world_view() -> None:
    text = call(build_server(FakeClient(DECISION)), "next_decision", wait_seconds=0)
    payload = json.loads(text)
    assert payload["decision_id"] == "base.production-1"
    assert payload["world_view"]["action_space"][0]["id"] == "unit:0"


def test_nothing_waiting_reads_as_a_sentence_not_an_error() -> None:
    """An agent that wakes with nothing to do must be able to tell that from a fault — a raised
    tool error here would read as "the game is broken" and change what it does next."""
    text = call(build_server(FakeClient()), "next_decision", wait_seconds=0)
    assert "No decision is waiting" in text
    assert "not reached a decision point" in text


def test_submit_passes_the_choice_through() -> None:
    client = FakeClient(DECISION)
    text = call(
        build_server(client),
        "submit_orders",
        decision_id="base.production-1",
        action_id="unit:0",
        reason="expand",
    )
    assert client.submitted == [("base.production-1", "unit:0", "expand")]
    assert json.loads(text)["applied"] == ["unit:0"]


def test_an_empty_reason_is_omitted_rather_than_recorded_as_blank() -> None:
    client = FakeClient(DECISION)
    call(build_server(client), "submit_orders", decision_id="d-1", action_id="unit:0")
    assert client.submitted[0][2] is None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"decision_id": "", "action_id": "unit:0"}, "next_decision"),
        ({"decision_id": "d-1", "action_id": ""}, "action space"),
    ],
)
def test_missing_arguments_tell_the_model_what_to_do(kwargs: dict, expected: str) -> None:
    """The error text is the whole value here: it is the model's next instruction."""
    with pytest.raises(Exception, match=expected):
        call(build_server(FakeClient()), "submit_orders", **kwargs)


def test_an_unreachable_orchestrator_says_so_in_words() -> None:
    """The commonest operational failure — the service is not running — must arrive as
    something a person reading the pane can diagnose."""
    client = FakeClient()
    client.raises = AgentError("orchestrator unreachable at http://fake: connection refused")
    with pytest.raises(Exception, match="unreachable"):
        call(build_server(client), "next_decision", wait_seconds=0)


def test_the_measurement_channels_reach_the_orchestrator() -> None:
    """`cited`, `followed` and `overrode` are how an agent-driven run stays measurable.

    Without them a record shows zero grounding utilisation and zero directive attention, which
    is indistinguishable from a model that read the facts and the plan and ignored both. The
    pivot to an agent brain silently zeroed all three until these were wired.
    """
    client = FakeClient(DECISION)
    call(
        build_server(client),
        "submit_orders",
        decision_id="d-1",
        action_id="unit:0",
        cited=["fac:recycling-tanks"],
        followed=["fund-weather-paradigm"],
        overrode=["expand-fast"],
    )
    assert client.extras[0]["cited"] == ["fac:recycling-tanks"]
    assert client.extras[0]["followed"] == ["fund-weather-paradigm"]
    assert client.extras[0]["overrode"] == ["expand-fast"]


def test_omitted_measurement_channels_send_empty_lists_not_none() -> None:
    client = FakeClient(DECISION)
    call(build_server(client), "submit_orders", decision_id="d-1", action_id="unit:0")
    assert client.extras[0] == {"cited": [], "followed": [], "overrode": []}


def test_issue_directive_passes_a_checkable_plan_through() -> None:
    client = FakeClient(DECISION)
    call(
        build_server(client),
        "issue_directive",
        decision_id="d-1",
        id="fund-weather-paradigm",
        intent="save energy for the Weather Paradigm",
        metric="energy_reserves",
        comparator="at_least",
        target=300,
        priority=7,
        entities=["fac:the-weather-paradigm"],
    )
    issued = client.directives[0]
    assert issued["metric"] == "energy_reserves"
    assert issued["target"] == 300
    assert issued["priority"] == 7
    assert issued["entities"] == ["fac:the-weather-paradigm"]


def test_a_relative_directive_sends_no_target() -> None:
    """The relative comparators measure against a baseline the orchestrator stamps. Sending a
    target would be the model inventing a number it was not asked for."""
    client = FakeClient(DECISION)
    call(
        build_server(client),
        "issue_directive",
        decision_id="d-1",
        id="grow-labs",
        intent="raise research output",
        metric="labs_output",
        comparator="increase",
    )
    assert "target" not in client.directives[0]


def test_the_directive_tool_says_what_makes_a_plan_checkable() -> None:
    """The constraint is the feature, and the model only learns it from the tool description."""
    described = (tools(build_server(FakeClient()))["issue_directive"].description or "").lower()
    assert "metrics" in described, "must say the metric has to be one the world view reports"
    assert "at_least" in described, "must enumerate the comparators"
    assert "before submit_orders" in described, "ordering is load-bearing"


def test_submit_says_what_cited_is_for() -> None:
    """`Orders.cited` notes that explaining this only in a system prompt left it empty on every
    run. The tool description is where a model actually reads it."""
    described = (tools(build_server(FakeClient()))["submit_orders"].description or "").lower()
    assert "grounding" in described
    assert "cited" in described


def test_the_bulk_turn_reads_carry_the_faction_through_to_the_orchestrator() -> None:
    """The scope has to reach the HTTP call, not merely appear in the signature.

    A tool that accepts `faction_id` and then asks unscoped is the worst of both: the schema
    says the boundary is enforced and nothing enforces it. That is strictly worse than no
    argument at all, because it stops anyone looking.
    """
    client = FakeClient()
    server = build_server(client)
    call(server, "turn_forecast", faction_id=3, turn=103)
    call(server, "turn_plan_status", faction_id=3)
    call(server, "deferred_decisions", faction_id=3, full=True)
    assert client.turn_reads == [(3, 103)]
    assert client.plan_reads == [3]
    assert client.pending_reads == [(3, True)]


def test_the_forecast_tool_names_the_next_step() -> None:
    """A read tool that does not say what to do with what it read is a dead end.

    `next_decision` is already held to this (`test_tool_descriptions_state_the_legality_rule`),
    and the forecast needs it more: reading a whole turn and then answering it one decision at
    a time is the latency this mechanism exists to remove, reached by an agent doing nothing
    wrong.
    """
    described = tools(build_server(FakeClient()))
    forecast = (described["turn_forecast"].description or "").lower()
    assert "submit_turn_plan" in forecast
    # And the reverse direction: the planning tool must name a tool, never a raw HTTP route an
    # MCP client has no way to call. This is the defect that motivated the whole change.
    plan = described["submit_turn_plan"].description or ""
    assert "turn_forecast" in plan
    assert "POST /agent/turn" not in plan


def test_a_deferral_sweep_is_reachable_and_says_how_to_resolve_one() -> None:
    """Parking a decision is only half a mechanism if nothing brings it back.

    `defer` answers the engine with its own pick immediately, so nothing blocks and nothing
    complains — which means an agent that never sweeps has a game quietly played by the
    deterministic tier while every surface reports health. The tool has to exist AND say which
    verb retires one.
    """
    described = tools(build_server(FakeClient()))
    text = described["deferred_decisions"].description or ""
    assert "issue_order" in text, "must name the verb that resolves a deferral"
    assert "expires" in text.lower(), "must say what happens if the sweep never comes"
