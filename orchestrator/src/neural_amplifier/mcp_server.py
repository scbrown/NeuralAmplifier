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
                # json.loads is Any-typed, and every caller here treats the result as a
                # mapping. Asserting the shape at the boundary keeps that assumption in one
                # place instead of letting Any leak into every tool method's return.
                decoded = json.loads(response.read() or b"{}")
                return decoded if isinstance(decoded, dict) else {"result": decoded}
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

    def submit(
        self, decision_id: str, action_id: str, reason: str | None, **extra: Any
    ) -> dict[str, Any]:
        return self._call(
            "/agent/submit",
            {"decision_id": decision_id, "action_id": action_id, "reason": reason, **extra},
        )

    def directive(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("/agent/directive", payload)

    def waiting(self) -> dict[str, Any]:
        return self._call("/agent/waiting")

    def order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("/order", payload)

    def outcomes(self, cursor: int) -> dict[str, Any]:
        return self._call("/agent/outcomes", {"cursor": cursor})


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
    def submit_orders(
        decision_id: str,
        action_id: str,
        reason: str = "",
        cited: list[str] | None = None,
        followed: list[str] | None = None,
        overrode: list[str] | None = None,
    ) -> str:
        """Answer a decision by choosing one action from its action space.

        `action_id` must be one of the `id` values in that decision's `action_space` — the
        engine's list is the only legal set and an invented id is rejected, not applied.

        `reason` is recorded with the decision and is worth writing properly: it is what makes a
        game reviewable afterwards, and it is the only part of your thinking that survives.

        `cited` — ids from the world view's `grounding` block that actually informed this
        choice, e.g. `["fac:recycling-tanks"]`. Cite what you used and nothing else. This is
        the only evidence that the retrieved facts influenced the decision rather than merely
        preceding it: leave it empty and a decision the facts drove is indistinguishable from
        one that ignored them. Ids you were not offered are discarded, so guessing gains
        nothing.

        `followed` / `overrode` — ids from the world view's `directives` block that you obeyed
        or deliberately went against. Overriding a standing plan is legitimate — priorities
        exist so a decision can outrank one — but say so, because a plan quietly ignored and a
        plan consciously outweighed look identical afterwards.

        Returns what was actually applied, which may differ from what you asked for if the
        policy guard stripped a choice. Read it rather than assuming.
        """
        if not decision_id:
            raise AgentError("decision_id is required — call next_decision first")
        if not action_id:
            raise AgentError("action_id is required and must come from the action space")
        result = client.submit(
            decision_id,
            action_id,
            reason or None,
            cited=cited or [],
            followed=followed or [],
            overrode=overrode or [],
        )
        return json.dumps(result, indent=2)

    @server.tool()
    def issue_directive(
        decision_id: str,
        id: str,
        intent: str,
        metric: str,
        comparator: str,
        target: float | None = None,
        priority: int = 5,
        entities: list[str] | None = None,
        horizon_turn: int | None = None,
        rationale: str = "",
    ) -> str:
        """Set a standing plan that later decisions will be shown. Call this BEFORE submit_orders.

        For long-horizon decisions — a tech path, a social model — whose reasoning should
        outlive the turn that made it. Without a directive your conclusion dies with this
        response and the next base-production decision knows nothing about it.

        `metric` must name something the world view actually reports in its `metrics` block, and
        `comparator` is one of `at_least`, `at_most`, `increase`, `decrease`, `hold`. That
        constraint is the point: "keep energy reserves above 300" is a claim a later turn can
        check, and "play aggressively" is not. A metric outside the vocabulary is refused here,
        while you can still rewrite it.

        `target` is required for `at_least`/`at_most`; the relative comparators measure against a
        baseline stamped from this world view, so do not supply one.

        `priority` 1–10, and the scale means the same thing across decisions that never see each
        other: 9–10 survival, 7–8 a committed plan, 4–6 a preference worth real cost, 1–3 a
        tie-breaker.

        `entities` are datalinks ids the plan is about — `["fac:the-weather-paradigm"]` for a
        plan to fund that project. They are lookup keys, not decoration: a later decision that
        spends the resource you are saving finds this plan by walking out from them.
        """
        payload = {
            "decision_id": decision_id,
            "id": id,
            "intent": intent,
            "metric": metric,
            "comparator": comparator,
            "priority": priority,
            "entities": entities or [],
            "rationale": rationale or None,
        }
        if target is not None:
            payload["target"] = target
        if horizon_turn is not None:
            payload["horizon_turn"] = horizon_turn
        return json.dumps(client.directive(payload), indent=2)

    @server.tool()
    def decisions_waiting() -> str:
        """List decisions currently waiting for an answer, oldest first.

        For orienting after a reconnect or a compaction — if you are unsure whether you already
        answered something, this is how to find out without guessing.
        """
        return json.dumps(client.waiting(), indent=2)

    @server.tool()
    def issue_order(verb: str, args: list[int]) -> str:
        """Command a unit or base DIRECTLY, without waiting to be asked.

        `next_decision` gives you what the engine chose to ask about, one at a time, in its order.
        This is the other door: pick the unit or base you care about and order it now. It is what
        makes a dependent move possible — order the move that has to happen first, then the one
        that depends on it.

            issue_order("move",  [veh_id, x, y])     send a unit to a tile
            issue_order("skip",  [veh_id])           end that unit's turn
            issue_order("build", [base_id, item_id]) set a base's production

        READ THE STATUS. `ok` means the game did it. `refused` means the game ran your order and
        declined it — the detail says why, and retrying the same thing will fail the same way.
        `unknown` means **the order may or may not have happened**: it is not a failure and it is
        not a success, and re-issuing may double-apply. Check the board before you retry.
        `unavailable` means this game was not launched with ordering configured.

        Legality is the engine's call, not this tool's — an illegal move comes back `refused` with
        the engine's own reason rather than being guessed at here.
        """
        return json.dumps(client.order({"verb": verb, "args": args}), indent=2)

    @server.tool()
    def issue_orders(orders: list[dict[str, Any]]) -> str:
        """Issue SEVERAL orders in one round trip — use this to move an army.

            issue_orders([{"verb": "move", "args": [12, 40, 21]},
                          {"verb": "skip", "args": [13]}])

        The channel costs about a quarter-second per order, so fifty units ordered one at a time
        is twelve seconds of your turn. This sends them together.

        **Read `results`, not just `status`.** The envelope is `ok` only when EVERY order
        succeeded; a batch that half-worked reports `refused`, and the per-order entries are the
        only place that says which ones. `dropped` is non-zero if you sent more than the adapter
        will run in one tick — those were not executed.
        """
        return json.dumps(client.order({"orders": orders}), indent=2)

    @server.tool()
    def order_outcomes(cursor: int = 0) -> str:
        """Did the orders you gave actually take effect?

        You know what you submitted and what the orchestrator applied. Neither answers whether the
        ENGINE kept it: an order can pass every check and still be overwritten by a later engine
        path. This reports what the game did, including divergences — where the base is building
        something other than what was decided.

        Pass back the `cursor` you were given to see only what is new. A decision you have not
        heard about reads `unknown`, never `applied`.
        """
        return json.dumps(client.outcomes(cursor), indent=2)

    return server


def main(url: str | None = None) -> int:
    """Entry point for ``neural-amplifier mcp``."""
    endpoint = url or os.environ.get("NA_URL", "http://127.0.0.1:8000")
    build_server(OrchestratorClient(endpoint)).run()
    return 0
