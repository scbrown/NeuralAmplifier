"""Per-seat evidence (aegis-8fye.3, slice D).

In seat mode each seat's decisions and inputs are ALSO written under ``<run>/seat-<n>/``; the
run-level log and store stay the operator's merged view, unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.config import Seat
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.replay import WorldViewStore
from neural_amplifier.seats import SeatRouter
from neural_amplifier.service import create_app


def world_view(faction_id: int, turn: int, base_id: int) -> dict[str, Any]:
    """A fixture world view for one faction's base decision."""
    return {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.production",
        "turn": turn,
        "faction": f"F{faction_id}",
        "faction_id": faction_id,
        "base_id": base_id,
        "bases": [{"name": f"B{base_id}", "base_id": base_id}],
        "action_space": [
            {"id": "unit:0", "action": "Colony Pod"},
            {"id": "facility:4", "action": "Recycling Tanks"},
        ],
    }


def _scripted(action_id: str) -> ScriptedBrain:
    return ScriptedBrain(chooser=lambda wv: Orders(choices=[Choice(action_id=action_id)]))


SEATS = {1: Seat(1, "alice", "scripted"), 2: Seat(2, "bob", "scripted")}


def _env(monkeypatch: pytest.MonkeyPatch, run: Path) -> None:
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    monkeypatch.setenv("NA_RUN_DIR", str(run))
    monkeypatch.setenv("NA_DECISION_LOG", str(run / "orch" / "decisions.jsonl"))
    monkeypatch.setenv("NA_WORLD_VIEW_STORE", str(run / "orch" / "worldviews"))


def _play(client: TestClient) -> None:
    """Three turns, both factions deciding on two bases each, interleaved as a game would."""
    for turn in (10, 11, 12):
        for base in (100, 101):
            for fid in (1, 2):
                body = world_view(fid, turn, base + 10 * fid)
                response = client.post("/decide", json=body)
                assert response.status_code == 200, response.text


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_two_seat_game_splits_into_disjoint_seat_logs_whose_union_is_the_merged_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    router = SeatRouter(SEATS, build=lambda seat: _scripted(f"a{seat.faction_id}"))
    _play(TestClient(create_app(brain=router)))

    merged = _lines(tmp_path / "orch" / "decisions.jsonl")
    seat1 = _lines(tmp_path / "seat-1" / "decisions.jsonl")
    seat2 = _lines(tmp_path / "seat-2" / "decisions.jsonl")

    assert len(merged) == 12
    assert len(seat1) == len(seat2) == 6
    # Disjoint, and together exactly the merged log — same lines, same order within a seat.
    assert not set(seat1) & set(seat2)
    assert sorted(seat1 + seat2) == sorted(merged)
    assert seat1 == [line for line in merged if json.loads(line)["seat"] == 1]
    assert seat2 == [line for line in merged if json.loads(line)["seat"] == 2]

    # No seat-1 row names faction 2 (and vice versa), in the record or in its stored input.
    for seat, rows, other in ((1, seat1, 2), (2, seat2, 1)):
        views = tmp_path / f"seat-{seat}" / "worldviews"
        for line in rows:
            rec = json.loads(line)
            assert rec["seat"] == seat
            assert rec["faction"] == f"F{seat}"
            assert f"F{other}" not in line
            stored = views / f"{rec['world_view_hash'].replace(':', '-')}.json"
            assert json.loads(stored.read_text())["faction_id"] == seat

    # World views: each seat's store is its own faction's subset of the merged store.
    merged_views = {p.name for p in (tmp_path / "orch" / "worldviews").glob("*.json")}
    v1 = {p.name for p in (tmp_path / "seat-1" / "worldviews").glob("*.json")}
    v2 = {p.name for p in (tmp_path / "seat-2" / "worldviews").glob("*.json")}
    assert v1 and v2 and not v1 & v2
    assert v1 | v2 == merged_views
    for name in v1:
        assert (
            json.loads((tmp_path / "seat-1" / "worldviews" / name).read_text())["faction_id"] == 1
        )


def test_no_seats_creates_no_seat_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    _play(TestClient(create_app(brain=_scripted("unit:0"))))

    assert len(_lines(tmp_path / "orch" / "decisions.jsonl")) == 12
    assert list((tmp_path / "orch" / "worldviews").glob("*.json"))
    assert not list(tmp_path.glob("seat-*"))


def test_unseated_faction_stays_in_the_merged_view_only(
    tmp_path: Path, thinker_base: WorldView
) -> None:
    log = DecisionLog(tmp_path / "orch" / "decisions.jsonl")
    store = WorldViewStore(tmp_path / "orch" / "worldviews")
    log.split_by_seat(tmp_path)
    store.split_by_seat(tmp_path, SEATS)
    router = SeatRouter(SEATS, build=lambda seat: _scripted("a"))
    orch = Orchestrator(router, log=log, store=store)

    orch.decide(thinker_base.model_copy(update={"faction_id": 4}))
    orch.decide(thinker_base.model_copy(update={"faction_id": None}))

    assert len(_lines(log.path)) == 2
    assert not list(tmp_path.glob("seat-*"))
