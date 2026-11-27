"""The per-faction policy overlay, slice A (aegis-8fye.6).

The orchestrator half: a frozen knob registry that refuses what it does not know, a ``/turn``
reply that carries the overlay to the engine only for LLM-seated factions, a ``policy_hash`` on
every decision record, and a write-only guard — no seat-facing payload may carry Thinker's
computed plan state, which is omniscient.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier import faction_policy as fp
from neural_amplifier.config import Seat
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.faction_policy import (
    DEFAULT_POLICY_HASH,
    KNOBS,
    PLAN_STATE_FIELDS,
    KnobError,
    PolicyStore,
    policy_hash,
)
from neural_amplifier.seats import SEAT_HEADER, SeatRouter
from neural_amplifier.service import create_app


def world_view(faction_id: int, turn: int = 12) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.production",
        "turn": turn,
        "faction": f"F{faction_id}",
        "faction_id": faction_id,
        "base_id": 1,
        "units": [],
        "bases": [{"name": "B1", "base_id": 1}],
        "action_space": [{"id": "unit:0", "action": "Colony Pod"}],
    }


ANNOUNCE = {"turn": 12, "faction_id": 3, "expected": [{"surface_id": "base.production"}]}


@pytest.fixture
def seated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A service seating scripted brains at factions 3 and 5, logging every decision."""
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    monkeypatch.delenv("NA_RUN_DIR", raising=False)
    log = DecisionLog(tmp_path / "decisions.jsonl")
    router = SeatRouter({3: Seat(3, "three", "scripted"), 5: Seat(5, "five", "scripted")})
    app = create_app(brain=router, log=log, sinks=[])
    tokens = app.state.seat_tokens.tokens
    return {
        "client": TestClient(app),
        "log": tmp_path / "decisions.jsonl",
        "h3": {SEAT_HEADER: tokens[3]},
        "h5": {SEAT_HEADER: tokens[5]},
    }


# ------------------------------------------------------------------ A1: the frozen registry


def test_registry_covers_exactly_the_first_cut_fields() -> None:
    fields = {k.field for k in KNOBS.values()}
    assert fields == {
        "Faction.AI_fight",
        "Faction.AI_growth",
        "Faction.AI_tech",
        "Faction.AI_wealth",
        "Faction.AI_power",
        "Faction.player_flags",
        "Faction.base_id_attack_target",
        "Faction.goals",
    }
    with pytest.raises(TypeError):
        KNOBS["new.knob"] = KNOBS["focus.fight"]  # type: ignore[index]


def test_player_flag_subset_excludes_diplomatic_and_atrocity_bits() -> None:
    masks = {k.mask for k in KNOBS.values() if k.type == "flag"}
    # PFLAG_COOP_WITH_HUMAN, TEAM_UP_VS_HUMAN, COMMIT_ATROCITIES_WANTONLY, OBLITERATE_CAPTURED
    assert not masks & {0x400000, 0x800000, 0x1000000, 0x2000000}
    assert len(masks) == len([k for k in KNOBS.values() if k.type == "flag"])


@pytest.mark.parametrize(
    ("knob", "value"),
    [
        ("focus.nonsense", 1),
        ("focus.fight", 2),
        ("focus.fight", -2),
        ("focus.tech", -1),
        ("focus.tech", True),
        ("focus.tech", 0.5),
        ("strategy.sea_power", 1),
        ("target.base", 512),
        ("target.base", -2),
        ("target.goals", [{"type": "nuke", "x": 1, "y": 1, "priority": 1}]),
        ("target.goals", [{"type": "attack", "x": 1, "y": 1, "priority": 99}]),
        ("target.goals", [{"type": "attack", "x": 1, "y": 1, "priority": 1, "plans": 1}]),
        ("target.goals", [{"type": "attack", "x": 1, "y": 1, "priority": 1}] * 9),
    ],
)
def test_unknown_or_out_of_bounds_knobs_are_refused_at_set_time(knob: str, value: object) -> None:
    store = PolicyStore()
    with pytest.raises(KnobError):
        store.set_policy(3, knob, value, "test")
    assert store.knobs(3) == {}


def test_a_change_needs_a_reason_and_a_player_faction() -> None:
    store = PolicyStore()
    with pytest.raises(KnobError, match="reason"):
        store.set_policy(3, "focus.fight", 1, "  ")
    with pytest.raises(KnobError):
        store.set_policy(0, "focus.fight", 1, "native life is not a player")
    with pytest.raises(KnobError):
        store.set_policy(8, "focus.fight", 1, "MaxPlayerNum is out of range")


def test_set_supersede_and_clear() -> None:
    store = PolicyStore()
    store.set_policy(3, "focus.fight", 1, "push")
    out = store.set_policy(3, "focus.fight", -1, "turtle")
    assert out["superseded"] == 1 and out["value"] == -1
    store.set_policy(3, "focus.fight", None, "back to defaults")
    assert store.knobs(3) == {}


# ------------------------------------------------------------------ A2: the /turn reply


def test_empty_policy_reply_is_byte_identical_to_the_reply_before_policy(
    seated: dict[str, Any],
) -> None:
    resp = seated["client"].post("/turn", json=ANNOUNCE)
    assert resp.status_code == 200
    assert resp.content == b'{"turn":12,"expected":1}'


def test_reply_carries_the_overlay_with_a_stable_hash(seated: dict[str, Any]) -> None:
    c = seated["client"]
    goals = [{"type": "attack", "x": 10, "y": 20, "priority": 5}]
    for knob, value in (("target.goals", goals), ("focus.fight", 1), ("strategy.land_power", True)):
        r = c.post(
            "/agent/policy",
            headers=seated["h3"],
            json={"knob": knob, "value": value, "reason": "press the north"},
        )
        assert r.status_code == 200, r.text
    first = c.post("/turn", json=ANNOUNCE).json()
    second = c.post("/turn", json=ANNOUNCE).json()
    assert first == second
    assert first["na_policy"] == [
        {
            "faction_id": 3,
            "knobs": {"focus.fight": 1, "strategy.land_power": True, "target.goals": goals},
        }
    ]
    assert first["policy_hash"] == policy_hash(first["na_policy"])
    assert len(first["policy_hash"]) == 64
    # Insertion order does not move the hash: the same posture set the other way round.
    other = PolicyStore()
    other.set_policy(3, "strategy.land_power", True, "r")
    other.set_policy(3, "focus.fight", 1, "r")
    other.set_policy(3, "target.goals", goals, "r")
    assert other.reply({3})["policy_hash"] == first["policy_hash"]


def test_reply_holds_only_llm_seated_factions() -> None:
    store = PolicyStore()
    store.set_policy(3, "focus.tech", 1, "r")
    store.set_policy(4, "focus.tech", 1, "r")
    rows = store.reply({3, 5})["na_policy"]
    assert [r["faction_id"] for r in rows] == [3]  # type: ignore[index]
    assert store.reply(set()) == {}


def test_a_seat_sets_only_its_own_faction(seated: dict[str, Any]) -> None:
    c = seated["client"]
    r = c.post(
        "/agent/policy",
        headers=seated["h3"],
        json={"faction_id": 5, "knob": "focus.fight", "value": 1, "reason": "r"},
    )
    assert r.status_code == 403
    assert c.post("/agent/policy", json={"knob": "focus.fight", "value": 1}).status_code == 401
    bad = c.post(
        "/agent/policy",
        headers=seated["h3"],
        json={"knob": "focus.fight", "value": 7, "reason": "r"},
    )
    assert bad.status_code == 422
    assert c.get("/agent/policy", headers=seated["h5"]).json()["knobs"] == {}


# ------------------------------------------------------------------ A3: policy_hash on records


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_policy_hash_is_on_every_record_and_names_the_delivered_posture(
    seated: dict[str, Any],
) -> None:
    c = seated["client"]
    assert c.post("/decide", json=world_view(3)).status_code == 200
    c.post(
        "/agent/policy",
        headers=seated["h3"],
        json={"knob": "focus.growth", "value": 1, "reason": "expand"},
    )
    # Set but not yet delivered: the engine is still under the defaults.
    assert c.post("/decide", json=world_view(3)).status_code == 200
    reply = c.post("/turn", json=ANNOUNCE).json()
    assert c.post("/decide", json=world_view(3, turn=13)).status_code == 200
    assert c.post("/decide", json=world_view(5, turn=13)).status_code == 200

    hashes = [(r["faction"], r.get("policy_hash")) for r in _records(seated["log"])]
    delivered = policy_hash(reply["na_policy"][0]["knobs"])
    assert hashes == [
        ("F3", DEFAULT_POLICY_HASH),
        ("F3", DEFAULT_POLICY_HASH),
        ("F3", delivered),
        ("F5", DEFAULT_POLICY_HASH),
    ]


# ------------------------------------------------------------------ A4: the write-only guard


def _keys(obj: object) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(str(k))
            found |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _keys(v)
    return found


def test_no_seat_facing_payload_carries_plan_state(seated: dict[str, Any]) -> None:
    """Fails if Thinker's computed AIPlans state ever reaches a seat through the policy door.

    The announcement is built to TEMPT an echo: it carries plan fields the adapter might one day
    send, and the reply must not hand them back.
    """
    c = seated["client"]
    for name in KNOBS:
        assert name.split(".")[-1] not in PLAN_STATE_FIELDS, name
    tempting = {**ANNOUNCE, "plans": {"enemy_bases": 9}, "enemy_bases": 9, "main_region": 4}
    c.post(
        "/agent/policy",
        headers=seated["h3"],
        json={"knob": "target.base", "value": 41, "reason": "take it"},
    )
    payloads = [
        c.post("/turn", json=tempting).json(),
        c.get("/agent/policy", headers=seated["h3"]).json(),
        c.post(
            "/agent/policy",
            headers=seated["h3"],
            json={"knob": "focus.power", "value": 1, "reason": "arm"},
        ).json(),
    ]
    for payload in payloads:
        leaked = _keys(payload) & PLAN_STATE_FIELDS
        assert not leaked, f"plan state reached a seat: {sorted(leaked)} in {payload}"


def test_plan_state_list_is_not_a_knob_namespace() -> None:
    # A knob may not be minted under a computed-plan name, which would make a read of "what I
    # set" indistinguishable from a read of what Thinker derived.
    assert not {k.split(".")[-1] for k in KNOBS} & PLAN_STATE_FIELDS
    assert fp.registry()[0]["name"] == "focus.fight"
