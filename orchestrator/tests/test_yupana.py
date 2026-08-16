"""The board guard — `yupana.py`, Hank roles (c)/(d).

Split the way `test_quipu.py` is: the mapping and the ruling are pure and run everywhere, and a
handful of integration tests skip unless a yupana is actually reachable.

The properties worth defending are not "it calls the service". They are the three that decide
whether a guard is trustworthy: it can only **subtract**, it **degrades** rather than stalling a
turn, and a policy nobody evaluated is reported rather than counted as satisfied. The last is
the subtle one — a selector that has rotted away from the adapter's vocabulary matches zero
nodes and produces zero violations, which reads exactly like a clean board.

Verified live against yupana 0.6.0 built with `--features mcp,game-state`: an unaffordable hurry
is denied, the brain is re-asked with the policy's claim, and it picks the legal alternative.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Action, Choice, Orders, WorldView
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.yupana import (
    FACTION_NODE,
    VOCAB,
    McpClient,
    YupanaError,
    YupanaGuard,
    _first_message,
    _ruling,
    entities,
    proposed,
)

REPO = Path(__file__).resolve().parents[2]
YUPANA_URL = os.environ.get("NA_YUPANA_URL", "")

SOLVENCY = {
    "label": "reserves-stay-solvent",
    "targets": f"{VOCAB}FactionState",
    "claim": "An order must not drive energy reserves below zero",
    "boundary": "order",
    "effect": "deny",
    "selector": {"selector_lang": "graph-pattern", "evidence_source": f"?f a {VOCAB}FactionState"},
    "predicate": {
        "selector_lang": "graph-pattern",
        "match_type": "must-match",
        "evidence_source": f"?f a {VOCAB}FactionState ; {VOCAB}energy_reserves ?e | ?e >= 0",
    },
}


def hurry_view(reserves: float = 40, **extra: Any) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=42,
        faction="GAIANS",
        surface_id="base.hurry",
        action_space=[
            Action(id="hurry:now", action="Hurry production", effects={"energy_reserves": -81.0}),
            Action(id="hurry:none", action="Do not hurry"),
        ],
        metrics={"energy_reserves": reserves},
        **extra,
    )


class FakeClient:
    """Records calls and returns canned reports. No network, no yupana."""

    def __init__(self, report: dict[str, Any] | None = None, raises: Exception | None = None):
        self.report = report or {}
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        if self.raises is not None:
            raise self.raises
        return self.report if tool == "yupana_guard" else {"nodes_added": 1}


# --- the board we send ------------------------------------------------------


def test_metrics_land_on_a_faction_node() -> None:
    """`Action.effects` is denominated in the metrics vocabulary, so an order's declared
    consequences need a node that carries metrics. Without it every effect would name a node
    the board does not have, and the guard would evaluate an empty overlay and report cleanly."""
    (faction,) = [e for e in entities(hurry_view()) if e["name"] == FACTION_NODE]
    assert faction["type"] == f"{VOCAB}FactionState"
    assert faction["attrs"][f"{VOCAB}energy_reserves"] == 40
    assert faction["attrs"][f"{VOCAB}turn"] == 42


def test_every_name_is_prefixed() -> None:
    """Yupana performs no prefix expansion — a pattern naming `smac:garrisonCount` matches an
    attribute literally called that. Two spellings of one fact is the failure mode, and it is
    silent on the reading side: the selector matches nothing and the board looks clean."""
    view = hurry_view(bases=[{"id": "base:1", "isBorderBase": True, "garrisonCount": 2}])
    (base,) = [e for e in entities(view) if e["name"] == "base:1"]

    assert base["type"] == f"{VOCAB}BaseState"
    assert base["attrs"] == {f"{VOCAB}isBorderBase": True, f"{VOCAB}garrisonCount": 2}
    assert all(k.startswith(VOCAB) for e in entities(view) for k in e["attrs"])


def test_nested_values_are_dropped_not_flattened() -> None:
    """Yupana's pattern engine compares scalars. Flattening would invent a key like
    `base.garrison.count` that this repository then owns on both sides and syncs by hand."""
    view = hurry_view(bases=[{"id": "b", "pop": 4, "queue": {"item": "Former"}, "tiles": [1, 2]}])
    (base,) = [e for e in entities(view) if e["name"] == "b"]

    assert base["attrs"] == {f"{VOCAB}pop": 4}


def test_bases_and_units_both_arrive() -> None:
    view = hurry_view(bases=[{"id": "b1"}], units=[{"id": "u1"}])
    kinds = {e["name"]: e["type"] for e in entities(view)}

    assert kinds["b1"] == f"{VOCAB}BaseState"
    assert kinds["u1"] == f"{VOCAB}UnitState"


# --- the orders we send -----------------------------------------------------


def test_effects_come_from_the_action_space_not_the_answer() -> None:
    """An order's consequences are the adapter's statement about its own engine. Taking them
    from the brain's reply would let it describe its move as harmless and be believed."""
    (order,) = proposed(Orders(choices=[Choice(action_id="hurry:now")]), hurry_view())

    assert order["id"] == "hurry:now"
    # Absolute, not relative: yupana's set_attr states the post-order value.
    assert order["effects"] == [
        {"op": "set_attr", "id": FACTION_NODE, "key": f"{VOCAB}energy_reserves", "value": -41.0}
    ]


def test_an_action_with_no_declared_effects_is_still_an_order() -> None:
    """Judged against the board as it stands, which is right — and is why a policy can fire
    `pre_existing` on a move that changes nothing."""
    (order,) = proposed(Orders(choices=[Choice(action_id="hurry:none")]), hurry_view())
    assert order["effects"] == []


# --- the ruling we return ---------------------------------------------------


def test_a_violation_strips_exactly_the_orders_it_blames() -> None:
    ruling = _ruling(
        {
            "violations": [
                {
                    "policy": "solvency",
                    "claim": "reserves must not go negative",
                    "offending_order_ids": ["hurry:now"],
                    "detail": "?f=faction:self",
                    "pre_existing": False,
                }
            ]
        }
    )

    assert ruling.verdict == "deny"
    assert ruling.stripped == ("hurry:now",)
    assert "reserves must not go negative" in ruling.advisories[0]


def test_a_pre_existing_condition_never_strips() -> None:
    """Denying a move for a condition it did not cause is a false deny, and a false deny
    removes a legal and possibly correct option. Reported, never enforced."""
    ruling = _ruling(
        {
            "violations": [
                {
                    "policy": "garrison",
                    "claim": "a border base keeps a defender",
                    "offending_order_ids": [],
                    "detail": "?b=base:1",
                    "pre_existing": True,
                }
            ]
        }
    )

    assert ruling.stripped == ()
    assert ruling.verdict == "warn"
    assert "already true before these orders" in ruling.advisories[0]


def test_a_policy_nobody_evaluated_is_reported_not_assumed_satisfied() -> None:
    """The failure this forwards. A selector that has rotted away from the adapter's
    vocabulary matches zero nodes and produces zero violations, which reads exactly like a
    clean board. Unreported is uncheckable, never violated."""
    ruling = _ruling(
        {
            "unevaluated": [{"policy": "pact-honour", "reason": "selector_lang sparql"}],
            "vacuous": [{"policy": "garrison", "reason": "selector matched no node"}],
        }
    )

    assert ruling.verdict == "warn"
    assert ruling.stripped == ()
    assert any("not evaluated" in a and "pact-honour" in a for a in ruling.advisories)
    assert any("matched nothing" in a and "garrison" in a for a in ruling.advisories)


def test_a_clean_board_allows_silently() -> None:
    assert _ruling({"violations": [], "advisories": []}).verdict == "allow"


def test_warn_findings_never_strip() -> None:
    ruling = _ruling({"advisories": [{"claim": "thin garrison", "detail": "?b=base:2"}]})
    assert ruling.verdict == "warn"
    assert ruling.stripped == ()


def test_one_order_blamed_twice_is_stripped_once() -> None:
    ruling = _ruling(
        {
            "violations": [
                {"claim": "a", "offending_order_ids": ["x"], "pre_existing": False},
                {"claim": "b", "offending_order_ids": ["x"], "pre_existing": False},
            ]
        }
    )
    assert ruling.stripped == ("x",)


# --- degrading, which is the invariant ---------------------------------------


def test_an_unreachable_yupana_allows_and_says_so() -> None:
    """`knowledge.py`'s rule and invariant 9's: a decision never stalls on an advisory system.
    A dead guard allows, and the degradation is recorded so an absent guard stays visible."""
    guard = YupanaGuard(client=FakeClient(raises=YupanaError("connection refused")))
    ruling = guard.rule(Orders(choices=[Choice(action_id="hurry:now")]), hurry_view())

    assert ruling.verdict == "allow"
    assert ruling.degraded is True
    assert "connection refused" in (ruling.reason or "")


def test_an_unexpected_exception_degrades_too() -> None:
    """A guard must never be the reason a turn stalls, whatever went wrong inside it."""
    guard = YupanaGuard(client=FakeClient(raises=ValueError("kaboom")))
    ruling = guard.rule(Orders(choices=[Choice(action_id="hurry:now")]), hurry_view())

    assert ruling.verdict == "allow"
    assert ruling.degraded is True
    assert "ValueError" in (ruling.reason or "")


def test_no_url_configured_is_a_degraded_allow_not_a_crash() -> None:
    ruling = YupanaGuard(url="").rule(Orders(choices=[Choice(action_id="hurry:now")]), hurry_view())
    assert ruling.verdict == "allow"
    assert ruling.degraded is True


def test_an_empty_answer_is_not_sent_to_the_board() -> None:
    """Nothing proposed, nothing to judge. Ingesting a board to report a clean guard for a
    decision that made no move is a call and a claim, both unearned."""
    client = FakeClient()
    ruling = YupanaGuard(client=client).rule(Orders(), hurry_view())

    assert client.calls == []
    assert ruling.verdict == "allow"
    assert ruling.degraded is False


def test_the_board_is_written_private_never_shared() -> None:
    """A shared write carrying a faction is a fog leak — yupana refuses and counts it, and
    this faction's own view of the board is not the game's common knowledge."""
    client = FakeClient()
    YupanaGuard(client=client).rule(Orders(choices=[Choice(action_id="hurry:none")]), hurry_view())

    (tool, args) = client.calls[0]
    assert tool == "yupana_ingest"
    assert args["visibility"] == "private"
    assert args["faction_id"] == "GAIANS"


def test_policies_are_passed_per_call() -> None:
    """Not held resident by yupana: they are authored in Quipu and projected, and a resident
    copy would enforce yesterday's governance while looking current."""
    client = FakeClient()
    YupanaGuard(client=client, policies=[SOLVENCY]).rule(
        Orders(choices=[Choice(action_id="hurry:now")]), hurry_view()
    )

    guard_call = dict(client.calls)["yupana_guard"]
    assert guard_call["policies"] == [SOLVENCY]


# --- the transport ----------------------------------------------------------


def test_an_sse_framed_reply_is_read() -> None:
    """yupana answers streamable-HTTP with `data:` frames rather than a bare JSON body."""
    assert _first_message('data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\nid: 0/0') == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }


def test_a_plain_json_reply_is_read_too() -> None:
    """The same server may answer either way, and the spec allows it."""
    assert _first_message('{"jsonrpc":"2.0","result":{}}') == {"jsonrpc": "2.0", "result": {}}


def test_an_unparseable_body_is_none_rather_than_an_exception() -> None:
    assert _first_message("") is None
    assert _first_message("not json at all") is None
    assert _first_message("data: {oops") is None


def test_a_jsonrpc_error_becomes_a_yupana_error() -> None:
    """Which the guard turns into a degraded allow — the shape every failure here takes."""

    class Erroring(McpClient):
        def _post(self, body: dict[str, Any]):  # type: ignore[override]
            if body.get("method") == "initialize":
                return {"result": {}}, "session-1"
            return {"error": {"message": "unknown variant `must`"}}, None

    with pytest.raises(YupanaError, match="unknown variant"):
        Erroring("http://x/mcp").call("yupana_guard", {})


# --- live, against a real yupana --------------------------------------------

live = pytest.mark.skipif(
    not YUPANA_URL, reason="set NA_YUPANA_URL to a yupana built with --features mcp,game-state"
)


@live
def test_an_unaffordable_order_is_denied_by_the_real_service() -> None:
    guard = YupanaGuard(url=YUPANA_URL, policies=[SOLVENCY], game_id="test-solvency")
    ruling = guard.rule(Orders(choices=[Choice(action_id="hurry:now")]), hurry_view(reserves=40))

    assert ruling.degraded is False
    assert ruling.verdict == "deny"
    assert ruling.stripped == ("hurry:now",)


@live
def test_the_same_order_is_allowed_when_it_can_be_paid_for() -> None:
    """The other half, and the one that catches a guard that denies everything."""
    guard = YupanaGuard(url=YUPANA_URL, policies=[SOLVENCY], game_id="test-solvency")
    ruling = guard.rule(Orders(choices=[Choice(action_id="hurry:now")]), hurry_view(reserves=400))

    assert ruling.degraded is False
    assert ruling.verdict == "allow"


@live
def test_a_denial_drives_a_repair_rather_than_losing_the_turn() -> None:
    """End to end, which is the only way to know the seam is real: the board guard denies, the
    brain is told the policy's claim, and it answers again — one decision, not a lost turn."""
    guard = YupanaGuard(url=YUPANA_URL, policies=[SOLVENCY], game_id="test-repair")
    brain = ScriptedBrain(
        responses=[
            Orders(choices=[Choice(action_id="hurry:now")]),
            Orders(choices=[Choice(action_id="hurry:none")]),
        ]
    )

    result = Orchestrator(brain, guard=guard).decide(hurry_view(reserves=40))

    assert len(brain.calls) == 2
    assert "energy reserves" in " ".join(brain.calls[1].advisories or [])
    assert [c.action_id for c in result.orders.choices] == ["hurry:none"]
    assert result.record.degraded is False
    assert result.record.repairs == 1


# --- loading the policy set -------------------------------------------------


def test_a_relative_policy_path_resolves_from_the_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """`just play` runs the service with `--directory orchestrator`, so the obvious
    `NA_YUPANA_POLICIES=policies/board.example.json` typed at the repo root resolves one level
    too deep — and the failure mode is a guard that silently evaluates nothing."""
    from neural_amplifier.service import load_policies

    monkeypatch.setenv("NA_YUPANA_POLICIES", "policies/board.example.json")
    monkeypatch.chdir(REPO / "orchestrator")

    labels = [p["label"] for p in load_policies()]
    assert "reserves-stay-solvent" in labels


def test_an_unreadable_policy_file_guards_with_none_rather_than_refusing_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reported and empty, not an exception. The guard then says "policy not evaluated" on every
    decision, which is the honest reading — and is exactly what yupana's own `unevaluated`
    exists to preserve."""
    from neural_amplifier.service import load_policies

    monkeypatch.setenv("NA_YUPANA_POLICIES", str(tmp_path / "nope.json"))
    assert load_policies() == []

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("NA_YUPANA_POLICIES", str(broken))
    assert load_policies() == []


def test_the_starter_policies_are_shaped_the_way_yupana_demands() -> None:
    """The three field values yupana refuses outright, caught here rather than as an
    `unevaluated` note on every decision of a real game."""
    import json as _json

    policies = _json.loads((REPO / "policies" / "board.example.json").read_text())
    assert policies
    for policy in policies:
        assert policy["boundary"] == "order"
        assert policy["effect"] in {"deny", "warn"}
        assert policy["selector"]["selector_lang"] == "graph-pattern"
        assert policy["predicate"]["selector_lang"] == "graph-pattern"
        assert policy["predicate"]["match_type"] in {"must-match", "must-not-match", "must-exist"}
        # Byte-for-byte vocabulary: a pattern naming an unprefixed attribute matches nothing.
        assert VOCAB in policy["selector"]["evidence_source"]


def test_the_guard_joins_the_chain_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A board guard is a network call inside the decision loop, so it is opt-in. Absent
    NA_YUPANA_URL the two local guards are exactly what they were."""
    from neural_amplifier.service import build_guard

    monkeypatch.delenv("NA_YUPANA_URL", raising=False)
    without = build_guard(retriever=None)
    monkeypatch.setenv("NA_YUPANA_URL", "http://127.0.0.1:3040")
    with_board = build_guard(retriever=None)

    assert len(with_board.guards) == len(without.guards) + 1
    assert type(with_board.guards[-1]).__name__ == "YupanaGuard"
