"""The MCP surface: how an agent plays the game.

It began as three tools, because a decision has three moments — find out one is waiting, read
it, answer it. The rule that kept it small is not the count but its reason: **no tool may offer
a second source of BOARD state.** What the rules say and what the board looks like are already
in the world view the agent collects, fog-gated and recorded, and every measurement here assumes
the world view is what the brain saw. A tool handing back tiles or another faction's bases would
let a model reason from something nothing gated and nothing recorded.

The bulk-turn reads (`turn_forecast`, `turn_plan_status`, `deferred_decisions`) are on the
right side of that line, and the distinction is worth stating because it is easy to get wrong.
They report the ORCHESTRATOR'S OWN RECORD of its decisions — what it was told to expect, what it
answered from a table, what it parked — not the game's board. Nothing in them exists that the
adapter did not already send. And each is scoped to the deciding faction, because the store
behind them holds all six factions together: `POST /agent/turn` refuses to run without a
faction, precisely so that a forgotten argument cannot read like a deliberate one.

Without them the loop this surface exists to serve was unreachable from it. `submit_turn_plan`
told the model to "read the turn forecast first (`POST /agent/turn`)" — an instruction an MCP
client cannot follow, because it holds tools and not HTTP verbs.

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
from urllib import error, parse, request

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
        return self._send(req)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """A read, spelled the way the orchestrator already spells it.

        The alternative was a POST alias beside each of these routes so that one verb covered
        everything. That is a second spelling of one thing, and this codebase has paid for those
        before — `fallback_action_id` next to `standing_action_id` cost a wrong answer in the
        one field an expired deferral uses. Nine lines of urllib is the cheaper side of that
        trade.
        """
        query = ""
        if params:
            live = {k: v for k, v in params.items() if v is not None}
            if live:
                query = "?" + parse.urlencode(live)
        return self._send(request.Request(f"{self.url}{path}{query}", method="GET"))

    def _send(self, req: request.Request) -> dict[str, Any]:
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

    def whatif(self, decision_id: str, action_id: str) -> dict[str, Any]:
        return self._call("/agent/whatif", {"decision_id": decision_id, "action_id": action_id})

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("/agent/plan", payload)

    def turn(self, faction_id: int, turn: int | None = None) -> dict[str, Any]:
        return self._call("/agent/turn", {"faction_id": faction_id, "turn": turn})

    def plan_status(self, faction_id: int) -> dict[str, Any]:
        return self._get("/agent/plan", {"faction_id": faction_id})

    def pending(self, faction_id: int, full: bool = False) -> dict[str, Any]:
        return self._get(
            "/agent/pending", {"faction_id": faction_id, "full": "true" if full else None}
        )


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
    def what_if(decision_id: str, action_id: str) -> str:
        """Try an option on the board before you commit to it.

        Speculative and free of consequence: the move is applied to a copy of this faction's
        board, its reach is reported, and nothing is committed. It does not answer or claim the
        decision, so you can ask about three options and then submit a fourth.

        What comes back is STRUCTURAL — which board entities the move changes and what those
        changes reach, ranked nearest-then-largest. It is not a verdict and not a
        recommendation: "this move touches these four bases" is a fact, "this move is reckless"
        is a judgement, and only the first is here. The guard is what holds judgements, as
        policies, and it runs on submit whether or not you asked this.

        `unavailable` with a reason means no board is attached to this game, or the action id is
        not one this decision offered. That is an answer, not an error — carry on and decide.
        """
        return json.dumps(client.whatif(decision_id, action_id), indent=2)

    @server.tool()
    def issue_order(verb: str, args: list[int], intent: dict[str, Any] | None = None) -> str:
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

        `intent` is optional, for a LONG-HORIZON order: why you gave it, and what should bring it
        back for review. The engine keeps the goto; this keeps the reason, in your faction's own
        graph, and recall puts it in front of any later decision naming that unit.

            issue_order("move", [12, 40, 21], intent={
                "faction_id": 2, "unit_id": 12,
                "goal": "hold the land bridge", "until_turn": 60,
                "triggers": [{"metric": "military_units",
                              "comparator": "at_least", "target": 4}]})

        It is recorded only if the game CONFIRMS the order, and refused up front (nothing issued)
        if it has no horizon or no trigger — an intent nothing can bring back for review would
        read forever as a plan somebody is watching.
        """
        payload: dict[str, Any] = {"verb": verb, "args": args}
        if intent:
            payload["intent"] = intent
        return json.dumps(client.order(payload), indent=2)

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

        An entry may carry an `intent` — same shape as `issue_order`'s — and each is recorded
        only if ITS order was individually confirmed. The reply's `intents` list says which were
        written, by position. A batch of moves with intents is how an army gets both ordered and
        explained in one round trip.
        """
        return json.dumps(client.order({"orders": orders}), indent=2)

    @server.tool()
    def turn_forecast(faction_id: int, turn: int | None = None) -> str:
        """Every decision your faction's turn is expected to raise — read this before planning.

        `next_decision` hands you one decision at a time, in the engine's order, and that is
        genuinely all it can do: when your first base is asked, the rest have not been POSTed
        and do not exist to the queue yet. You cannot spend a limited mineral pool where it
        matters most across fifty bases while seeing one of them. This is the same turn whole,
        forecast by the adapter at the between-turns seam, and it is step one of bulk-turn
        mode — decide it all, then `submit_turn_plan`.

        Read `status` per entry, not just the list. `expected` means forecast and not yet
        raised; `raised` means the engine is asking now; `answered`, `applied` and `diverged`
        are already behind you. And read `unraised`: a forecast is made from the board as it
        stood when the last turn ENDED, so a base that was captured or finished a project never
        raises the decision it was expected to. Planning an entry for one is harmless — it
        simply never fires — but waiting for one is a wait that never ends.

        `faction_id` is required and it is not paperwork: the turn is scoped to you, and an
        unscoped read would show you the other factions' bases by name.
        """
        return json.dumps(client.turn(faction_id, turn), indent=2)

    @server.tool()
    def turn_plan_status(faction_id: int) -> str:
        """Did your turn plan actually answer anything — and what did it miss?

        `applied` per entry counts the decisions that entry answered. It is legitimately more
        than one: the engine re-asks a base within a turn.

        `missed` is the number worth reading. Each entry there named an action that had left the
        action space by the time its decision arrived — the item was already built, or a
        prerequisite went. A table that misses often is strategy set too early in the turn, not
        a broken plan, and the fix is to plan nearer the decisions rather than to plan less.

        An entry with `applied: 0` and no miss simply never came up. Compare it against
        `unraised` in `turn_forecast` before treating it as a fault.
        """
        return json.dumps(client.plan_status(faction_id), indent=2)

    @server.tool()
    def deferred_decisions(faction_id: int, full: bool = False) -> str:
        """The decisions you parked with `defer`, and have not come back to yet.

        Deferring answered the engine immediately with its own pick, so the game never blocked
        and nothing is waiting on you — but the engine's pick is standing, and it stands for
        good if you never return. These are the ones to sweep once per turn.

        `full=true` also returns the grounded world view you read when you deferred. Use it.
        Resolving from a different set of facts than you deferred on is a differently-uninformed
        answer, not a better one.

        Resolve through `issue_order(verb="build", args=[base_id, item_id])`: a confirmed build
        closes the matching deferral and names it in that order's response. Unresolved by the
        next turn the deferral expires, honestly recorded, with the engine's choice standing.
        """
        return json.dumps(client.pending(faction_id, full), indent=2)

    @server.tool()
    def submit_turn_plan(faction_id: int, turn: int, entries: list[dict[str, Any]]) -> str:
        """Set your whole turn's answers at once — bulk-turn mode.

        Read the turn forecast first (`turn_forecast(faction_id, turn)` — it lists every decision
        the turn is expected to raise), decide them all at your own pace, then install the table:

            submit_turn_plan(faction_id=2, turn=43, entries=[
                {"surface_id": "base.production", "base_id": 7,
                 "action_id": "facility:4", "reason": "finish the Tanks"},
                {"surface_id": "faction.se", "action_id": "se:no-change"}])

        Covered decisions are answered from the table in milliseconds, recorded at tier "plan";
        you are woken only for decisions the table does not cover, and for a planned action the
        engine stopped offering (the wake-up names it). The table is valid for exactly the turn
        you state and replaces your previous one whole — install a new table each turn, and an
        empty `entries` list means "wake me for everything".

        `turn_plan_status(faction_id)` afterwards says what answered and what missed.
        """
        return json.dumps(
            client.plan({"faction_id": faction_id, "turn": turn, "entries": entries}), indent=2
        )

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
