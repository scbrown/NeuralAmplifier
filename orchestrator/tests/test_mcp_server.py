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
        self.raises: Exception | None = None

    def next_decision(self, wait: float) -> dict[str, Any]:
        if self.raises:
            raise self.raises
        return self.decision or {"decision_id": None, "waiting": 0}

    def submit(self, decision_id: str, action_id: str, reason: str | None) -> dict[str, Any]:
        if self.raises:
            raise self.raises
        self.submitted.append((decision_id, action_id, reason))
        return {"decision_id": decision_id, "accepted": action_id, "status": "applied to the game"}

    def waiting(self) -> dict[str, Any]:
        return {"waiting": []}


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


def test_the_surface_is_three_tools() -> None:
    """Find out, read, answer. Anything more invites the model to go looking for game state
    instead of reading the world view it was handed."""
    assert set(tools(build_server(FakeClient()))) == {
        "next_decision",
        "submit_orders",
        "decisions_waiting",
    }


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
    assert json.loads(text)["accepted"] == "unit:0"


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
