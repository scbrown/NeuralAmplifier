"""The board guard, served by yupana — ``docs/knowledge-architecture.md``, Hank roles (c)/(d).

``hank.StateGuard`` is arithmetic on figures the world view already declares, and that is its
ceiling: it can answer "can this faction afford this order" and nothing that needs a board. The
policies the architecture actually asks for — garrison a border base, hold expansion under
threat, don't break a pact without casus belli — are statements about *entities and their
relations*, and there was nowhere to evaluate them.

Yupana is that somewhere. It holds a hot, per-tenant, copy-on-write board graph and evaluates
graph-pattern policies at the order boundary: ``yupana_ingest`` writes this turn's board,
``yupana_guard`` applies the proposed orders to an overlay and reports what breaks. This module
is the seam — it speaks yupana's MCP surface and returns a :class:`~.knowledge.Ruling`, so the
orchestrator gains a real board guard without learning what a board graph is.

**It can only subtract.** The module map's rule for this file's neighbour holds here without
exception: a guard narrows what is already legal and never widens it. Nothing yupana says can
add an action, and a finding that names no order strips nothing.

**A dead guard allows.** Every failure — yupana down, the ``game-state`` feature not built, a
malformed reply, a timeout — returns a degraded ``allow``. That is ``knowledge.py``'s rule and
invariant 9's: a decision must never stall on an advisory system. The degradation is recorded,
so an absent guard is visible rather than silently permissive.

**Three things reported that a violation list cannot say**, and this module forwards all three
rather than reducing them to a verdict. A policy yupana could not evaluate (``unevaluated``), and
one whose selector matched nothing (``vacuous``), are *not* satisfied policies — a selector that
has rotted away from the adapter's vocabulary matches zero nodes and produces zero violations,
which reads exactly like a clean board. They arrive as advisories, because unreported is
uncheckable, never violated. The third is ``pre_existing``: yupana blames no order for a
condition that already held before the orders, and denying a move for something it did not cause
is a false deny that removes a legal and possibly correct move.

**Vocabulary is byte-for-byte.** Yupana performs no prefix expansion — an attribute named
``smac:garrisonCount`` in a pattern matches an attribute literally called ``smac:garrisonCount``
and nothing else. So this module prefixes every ingested name with :data:`VOCAB` and the policies
must be written against the same spelling. Getting it wrong is not silent: the selector matches
nothing and yupana says so under ``vacuous``, which is how the first version of this seam was
caught.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from .contract import Choice, Orders, WorldView
from .knowledge import Ruling

#: Every node kind and attribute name this module ingests is prefixed with this, and the
#: policies are written against the same spelling. Yupana matches names literally, so the one
#: thing that must not happen is two spellings of the same fact.
VOCAB = "smac:"

#: The node id carrying this faction's turn-level measurements. Metrics are what
#: ``Action.effects`` speaks in, so a policy about affordability or reserves is a policy about
#: this node.
FACTION_NODE = "faction:self"


class YupanaError(RuntimeError):
    """Yupana could not be reached or refused the call. Always degrades, never propagates."""


def _attrs(source: dict[str, Any], skip: tuple[str, ...] = ()) -> dict[str, Any]:
    """Scalar attributes only, prefixed.

    Yupana's pattern engine compares scalars; a nested object has nothing it could be compared
    against, and passing one through would put a value in the graph that no policy can name.
    Dropped rather than flattened: an invented key like ``base.garrison.count`` is a vocabulary
    this repository would then own on both sides and keep in sync by hand.

    ``skip`` is how ``id`` stays out. It is already the node's name, and carrying it as an
    attribute too would be two spellings of one fact — the exact thing this module's byte-for-
    byte vocabulary rule exists to prevent, and the one a policy author is most likely to reach
    for the wrong half of.
    """
    out: dict[str, Any] = {}
    for key, value in source.items():
        if key not in skip and isinstance(value, bool | int | float | str):
            out[f"{VOCAB}{key}"] = value
    return out


def entities(world_view: WorldView) -> list[dict[str, Any]]:
    """This turn's board as yupana entities.

    Three kinds, and the faction node is the one that is not obvious. ``Action.effects`` is
    denominated in the metrics vocabulary, so an order's declared consequences land on a node
    that carries metrics — without it, every effect would name a node the board does not have
    and the guard would evaluate an empty overlay while reporting cleanly.
    """
    out: list[dict[str, Any]] = []

    metrics = world_view.metrics or {}
    out.append(
        {
            "name": FACTION_NODE,
            "type": f"{VOCAB}FactionState",
            "description": world_view.faction,
            "attrs": {**_attrs(metrics), f"{VOCAB}turn": world_view.turn},
        }
    )

    for i, base in enumerate(world_view.bases or []):
        out.append(
            {
                "name": str(base.get("id", f"base:{i}")),
                "type": f"{VOCAB}BaseState",
                "attrs": _attrs(base, skip=("id",)),
            }
        )
    for i, unit in enumerate(world_view.units or []):
        out.append(
            {
                "name": str(unit.get("id", f"unit:{i}")),
                "type": f"{VOCAB}UnitState",
                "attrs": _attrs(unit, skip=("id",)),
            }
        )
    return out


def proposed(orders: Orders, world_view: WorldView) -> list[dict[str, Any]]:
    """The choices, each carrying the board deltas the *engine* declared for it.

    The effects come from the action space, never from the model: an order's consequences are
    the adapter's statement about its own engine, and taking them from the answer would let a
    brain describe its move as harmless and be believed. A choice whose action declares no
    effects still becomes an order with none — it is judged against the board as it stands,
    which is right, and it is why a policy can fire ``pre_existing``.
    """
    by_id = {action.id: action for action in world_view.action_space}
    out: list[dict[str, Any]] = []
    for choice in orders.choices:
        action = by_id.get(choice.action_id)
        effects: list[dict[str, Any]] = []
        metrics = world_view.metrics or {}
        for key, delta in (action.effects or {}).items() if action else ():
            # Absolute, not relative: yupana's `set_attr` states the post-order value, and the
            # pre-order value is the one the adapter published this turn.
            base = metrics.get(key)
            if isinstance(base, bool | int | float):
                effects.append(
                    {
                        "op": "set_attr",
                        "id": FACTION_NODE,
                        "key": f"{VOCAB}{key}",
                        "value": base + delta,
                    }
                )
        out.append(
            {"id": choice.action_id, "kind": world_view.surface_id or "DECIDE", "effects": effects}
        )
    return out


class McpClient:
    """The smallest streamable-HTTP MCP client that can call one tool.

    Hand-rolled on ``urllib`` for the same reason :class:`~.datalinks.quipu.QuipuRetriever` is:
    the guard runs inside a synchronous decision loop that must not stall, and the official
    client is async, so using it would mean spinning an event loop per decision inside a
    threadpool worker. What is needed here is three JSON-RPC calls and an SSE frame reader.

    The session is established lazily and reused. A server restart invalidates it, which shows
    up as an error on the next call and is handled the way every other failure is — degrade,
    record, and let the next decision re-establish.
    """

    def __init__(self, url: str, timeout: float = 2.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._session: str | None = None
        # Loopback: an ambient HTTPS_PROXY would otherwise route traffic that cannot be routed.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _post(self, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                # Both, because a streamable-HTTP server may answer either way and this one
                # answers with SSE frames.
                "accept": "application/json, text/event-stream",
                **({"mcp-session-id": self._session} if self._session else {}),
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                session = response.headers.get("mcp-session-id")
                raw = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise YupanaError(f"yupana unreachable at {self.url}: {exc}") from exc
        return _first_message(raw), session

    def connect(self) -> None:
        """`initialize`, then the `initialized` notification the spec requires before any call."""
        if self._session is not None:
            return
        message, session = self._post(
            {
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "neural-amplifier", "version": "0"},
                },
            }
        )
        if message is None or "result" not in message:
            raise YupanaError(f"yupana refused the handshake: {message}")
        self._session = session
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool and return its parsed JSON payload."""
        self.connect()
        message, _ = self._post(
            {
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        if message is None:
            raise YupanaError(f"{tool}: no reply")
        if "error" in message:
            raise YupanaError(f"{tool}: {message['error'].get('message', message['error'])}")
        content = (message.get("result") or {}).get("content") or []
        if not content:
            raise YupanaError(f"{tool}: empty reply")
        try:
            payload = json.loads(content[0].get("text", ""))
        except json.JSONDecodeError as exc:
            raise YupanaError(f"{tool}: reply was not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise YupanaError(f"{tool}: reply was not an object")
        return payload


def _first_message(raw: str) -> dict[str, Any] | None:
    """The first JSON-RPC message in a body that may be SSE-framed or plain JSON."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                parsed = json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


class YupanaGuard:
    """Board policies at the order boundary, evaluated by yupana.

    Implements the ``Guard`` protocol, so it composes into ``hank.GuardChain`` beside
    ``StateGuard`` and ``CitationGuard`` and needs no change anywhere else.
    """

    def __init__(
        self,
        url: str | None = None,
        policies: list[dict[str, Any]] | None = None,
        game_id: str = "na",
        timeout: float = 2.0,
        client: Any | None = None,
    ) -> None:
        # ``None`` means "take it from the environment"; ``""`` means "explicitly none". They
        # were the same thing until a test asked for a disabled guard inside a run that had
        # NA_YUPANA_URL set and silently got a live one. A caller that passes a value should
        # get the value it passed.
        self.url = (os.environ.get("NA_YUPANA_URL", "") if url is None else url).rstrip("/")
        #: Passed on every call rather than held resident by yupana, deliberately: policies are
        #: authored in Quipu and projected, and a resident copy would enforce yesterday's
        #: governance while looking current.
        self.policies = policies if policies is not None else []
        self.game_id = game_id
        self.client = client or (McpClient(f"{self.url}/mcp", timeout) if self.url else None)

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        if self.client is None:
            return Ruling(degraded=True, reason="no yupana configured")
        if not orders.choices:
            # Nothing proposed, nothing to judge. Asking anyway would ingest a board and report
            # a clean guard for a decision that made no move.
            return Ruling()

        try:
            self.client.call(
                "yupana_ingest",
                {
                    "game_id": self.game_id,
                    "faction_id": world_view.faction,
                    # Private, always. This faction's own view of the board is not the game's
                    # common knowledge, and a shared write carrying a faction is a fog leak —
                    # yupana refuses and counts it, which is a control we should never trip.
                    "visibility": "private",
                    # This world view IS the board, not a patch on the last one. Without it
                    # yupana's ingest merges, so a base razed twenty turns ago survives every
                    # later decision that simply does not list it and goes on matching policy
                    # selectors — the brain warned forever about a base it no longer owns.
                    # Needs yupana >= 0.6.1; an older one ignores the field and merges, which
                    # is the behaviour we had, so this degrades to the old bug rather than to
                    # an error.
                    "replace": True,
                    "entities": entities(world_view),
                    "edges": [],
                },
            )
            report = self.client.call(
                "yupana_guard",
                {
                    "game_id": self.game_id,
                    "faction_id": world_view.faction,
                    "policies": self.policies,
                    "orders": proposed(orders, world_view),
                },
            )
        except YupanaError as exc:
            return Ruling(degraded=True, reason=str(exc))
        except Exception as exc:  # a guard must never be the reason a turn stalls
            return Ruling(degraded=True, reason=f"{type(exc).__name__}: {exc}")

        return _ruling(report)


def what_if(guard: YupanaGuard, world_view: WorldView, action_id: str) -> dict[str, Any]:
    """What would this action change, and what do those changes reach — Hank role (e).

    The question the guard cannot answer. A guard says "this breaks a rule"; this says "here is
    what moves, and here is what it touches", which is what a player actually wants before
    committing. Speculative throughout: yupana applies the order to a copy-on-write overlay and
    nothing is committed, so asking is free of consequence.

    Structural only, and deliberately. Yupana ranks what a change reaches over the adapter's own
    vocabulary; it does not know that a base is "exposed" or a move "risky". Domain judgements
    are graph-pattern policies and belong in the guard — building them in here would put a second
    opinion about the game in a module whose whole value is having none.

    Returns yupana's report, or a ``{"unavailable": reason}`` envelope. Never raises: this is a
    convenience an agent calls mid-decision, and an unreachable yupana must cost the answer to
    one question rather than the turn.
    """
    if guard.client is None:
        return {"unavailable": "no yupana configured"}
    if not any(a.id == action_id for a in world_view.action_space):
        # The engine's action space is the whole truth about legality (invariant 1), and it is
        # also the whole truth about what there is to ask about. Speculating on an id nobody
        # offered would answer a question about a move that cannot be made.
        return {"unavailable": f"{action_id!r} is not in this decision's action space"}

    orders = proposed(Orders(choices=[Choice(action_id=action_id)]), world_view)
    try:
        guard.client.call(
            "yupana_ingest",
            {
                "game_id": guard.game_id,
                "faction_id": world_view.faction,
                "visibility": "private",
                # Same reason as the guard's, and here it is load-bearing for a different
                # rule: `what_if` may only ever speak about entities THIS world view carried,
                # or it becomes a second source of board state — which the MCP surface
                # explicitly forbids (test_mcp_server.py). Replace is what makes that closure
                # true rather than hoped for.
                "replace": True,
                "entities": entities(world_view),
                "edges": [],
            },
        )
        return guard.client.call(
            "yupana_whatif",
            {
                "game_id": guard.game_id,
                "faction_id": world_view.faction,
                "orders": orders,
            },
        )
    except YupanaError as exc:
        return {"unavailable": str(exc)}
    except Exception as exc:
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


def _ruling(report: dict[str, Any]) -> Ruling:
    """Yupana's report as a :class:`~.knowledge.Ruling`.

    Deny strips, warn does not, and a finding that blames no order strips nothing however
    severe it is — that is ``pre_existing``, a condition the orders did not cause.
    """
    stripped: list[str] = []
    advisories: list[str] = []

    for finding in report.get("violations") or []:
        blamed = [str(i) for i in finding.get("offending_order_ids") or []]
        claim = finding.get("claim") or finding.get("policy") or "policy violated"
        detail = finding.get("detail") or ""
        if finding.get("pre_existing") or not blamed:
            # Already true before the orders. Reported so the brain knows, never enforced:
            # denying a move for a condition it did not cause removes a legal, possibly
            # correct option and teaches the wrong lesson.
            advisories.append(f"{claim} — already true before these orders ({detail})")
            continue
        stripped.extend(blamed)
        advisories.append(f"{claim} ({detail})")

    for finding in report.get("advisories") or []:
        claim = finding.get("claim") or finding.get("policy") or "advisory"
        advisories.append(f"{claim} ({finding.get('detail', '')})")

    # Neither satisfied nor violated — nobody asked. Forwarded because a policy that was
    # silently skipped is indistinguishable from one that passed, which is how a rotted
    # selector reads as a clean board.
    for note in report.get("unevaluated") or []:
        advisories.append(f"policy not evaluated: {note.get('policy')} — {note.get('reason')}")
    for note in report.get("vacuous") or []:
        advisories.append(f"policy matched nothing: {note.get('policy')} — {note.get('reason')}")

    verdict = "deny" if stripped else ("warn" if advisories else "allow")
    return Ruling(
        verdict=verdict, stripped=tuple(dict.fromkeys(stripped)), advisories=tuple(advisories)
    )
