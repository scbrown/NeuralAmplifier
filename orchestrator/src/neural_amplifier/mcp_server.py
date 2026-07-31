"""The MCP surface: how an agent plays the game.

Three tools, because a decision has three moments — find out one is waiting, read it, answer it.
Everything else an agent might want (what the rules say, what the board looks like) is already
in the world view it collects, and adding a tool for it would be inviting the model to go
looking instead of reading what it was given.

This is the *contract in executable form*. ``docs/contract.md`` describes a world view and a set
of orders; these tools hand over one and accept the other. A harness that speaks MCP is
therefore plugged in as soon as it can call a tool — no adapter, no translation, and the same
validation on the way back in whichever client sent it.

Run as a stdio server, which is what ``claude mcp add`` expects and what makes a pane the unit
of integration::

    claude mcp add neural-amplifier -- uv run neural-amplifier mcp --url http://127.0.0.1:8000

It talks to a *running* orchestrator over HTTP rather than importing it. One source of truth for
the queue, several agents able to attach at once, and an MCP process that can be restarted
without dropping a game.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any
from urllib import error, request

#: How long to wait on the orchestrator's own endpoints. Generous because ``next_decision`` may
#: legitimately block server-side while a decision is on its way — the *game* is what this is
#: waiting for, and a turn can take a while.
HTTP_TIMEOUT = 120.0


class OrchestratorClient:
    """A thin HTTP client for the agent-facing endpoints.

    Deliberately urllib rather than a dependency: the MCP server is a small process that has to
    start fast and reliably in a pane, and three POSTs do not justify pulling a client library
    into the runtime path of a game.
    """

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def _call(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}).encode()
        req = request.Request(
            f"{self.url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
                return json.loads(response.read() or b"{}")
        except error.HTTPError as exc:
            # The orchestrator's rejections are the useful ones — "already answered", "not a
            # legal action" — and they have to reach the model as text it can act on rather
            # than as a stack trace. A tool error the model cannot read is a tool error it
            # cannot recover from.
            detail = exc.read().decode(errors="replace")
            # FastAPI wraps its messages in {"detail": ...}; anything else (a proxy's HTML
            # error page, say) is passed through as-is rather than discarded, because the
            # model reading it is better served by an ugly message than by none.
            with contextlib.suppress(ValueError, AttributeError):
                detail = json.loads(detail).get("detail", detail)
            raise AgentError(f"{exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise AgentError(f"orchestrator unreachable at {self.url}: {exc}") from exc

    def next_decision(self, wait: float) -> dict[str, Any]:
        return self._call("/agent/next", {"wait": wait})

    def submit(self, decision_id: str, action_id: str, reason: str | None) -> dict[str, Any]:
        return self._call(
            "/agent/submit",
            {"decision_id": decision_id, "action_id": action_id, "reason": reason},
        )

    def waiting(self) -> dict[str, Any]:
        return self._call("/agent/waiting")


class AgentError(RuntimeError):
    """Something the model should read and respond to, not a crash."""


def build_server(client: OrchestratorClient) -> Any:
    """Construct the MCP server. Imports the SDK lazily so the package works without it.

    ``MCPServer`` is the SDK's decorator-style server; it was ``FastMCP`` before mcp 2.0. The
    import is narrowed to the module that moved so that a *different* ImportError — a genuine
    bug inside this file, say — is not caught and reported as a missing dependency. That
    misreport cost real time here: the advice was to install a package that was already
    installed, which is the least useful error message available.
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] != "mcp":
            raise
        raise SystemExit("the MCP server needs the 'mcp' extra: uv sync --extra mcp") from exc

    server = MCPServer("neural-amplifier")

    @server.tool()
    def next_decision(wait_seconds: float = 0.0) -> str:
        """Collect the game decision that is waiting for you.

        Returns the full world view: the legal action space, the board and economy as the engine
        reports them, any retrieved rules and doctrine, and any standing directives with what
        each option would cost them. Everything needed to decide is in here — do not go looking
        elsewhere for the game state.

        Answer it with `submit_orders`, quoting the `decision_id` this returns. You may only
        choose an `id` that appears in `action_space`; anything else is refused.

        Set `wait_seconds` to block until a decision arrives if none is waiting yet.
        """
        payload = client.next_decision(wait_seconds)
        if not payload.get("decision_id"):
            return "No decision is waiting. The game has not reached a decision point yet."
        return json.dumps(payload, indent=2)

    @server.tool()
    def submit_orders(decision_id: str, action_id: str, reason: str = "") -> str:
        """Answer a decision by choosing one action from its action space.

        `action_id` must be one of the `id` values in that decision's `action_space` — the
        engine's list is the only legal set and an invented id is rejected, not applied.

        `reason` is recorded with the decision and is worth writing properly: it is what makes a
        game reviewable afterwards, and it is the only part of your thinking that survives.

        Returns what was actually applied, which may differ from what you asked for if the
        policy guard stripped a choice. Read it rather than assuming.
        """
        if not decision_id:
            raise AgentError("decision_id is required — call next_decision first")
        if not action_id:
            raise AgentError("action_id is required and must come from the action space")
        result = client.submit(decision_id, action_id, reason or None)
        return json.dumps(result, indent=2)

    @server.tool()
    def decisions_waiting() -> str:
        """List decisions currently waiting for an answer, oldest first.

        For orienting after a reconnect or a compaction — if you are unsure whether you already
        answered something, this is how to find out without guessing.
        """
        return json.dumps(client.waiting(), indent=2)

    return server


def main(url: str | None = None) -> int:
    """Entry point for ``neural-amplifier mcp``."""
    endpoint = url or os.environ.get("NA_URL", "http://127.0.0.1:8000")
    build_server(OrchestratorClient(endpoint)).run()
    return 0
