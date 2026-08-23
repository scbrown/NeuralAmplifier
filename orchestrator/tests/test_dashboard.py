from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.service import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def test_dashboard_is_read_only_projection_of_run_artifacts(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "decisions.jsonl"
    store_path = tmp_path / "worldviews"
    monkeypatch.setenv("NA_WORLD_VIEW_STORE", str(store_path))
    client = TestClient(create_app(brain=ScriptedBrain(), log=DecisionLog(log_path)))
    world = json.loads((FIXTURES / "thinker_base_production.json").read_text())

    assert client.post("/decide", json=world).status_code == 200
    live = client.get("/dashboard/api/live").json()
    decisions = client.get("/dashboard/api/decisions").json()

    assert live["decisions"] == 1
    assert live["turn"] == world["turn"]
    assert live["factions"][0]["name"] == world["faction"]
    assert decisions[0]["surface"] == "base.production"
    assert client.get(f"/dashboard/api/decisions/{decisions[0]['id']}").json()["action_space"]


def test_live_dashboard_merges_all_player_census_with_our_rich_metrics(
    tmp_path: Path, monkeypatch
) -> None:
    log_path = tmp_path / "decisions.jsonl"
    store_path = tmp_path / "worldviews"
    state_path = tmp_path / "na-command-result"
    state_path.write_text(
        json.dumps(
            {"detail": ("state=0x2 turn=123 player=7 bases=1:32,2:51,3:36,4:43,5:22,6:49,7:0")}
        )
    )
    monkeypatch.setenv("NA_WORLD_VIEW_STORE", str(store_path))
    monkeypatch.setenv("NA_DASHBOARD_GAME_STATE", str(state_path))
    client = TestClient(create_app(brain=ScriptedBrain(), log=DecisionLog(log_path)))
    world = json.loads((FIXTURES / "thinker_base_production.json").read_text())
    world["faction"] = "Peacekeepers"
    world["faction_id"] = 7
    assert client.post("/decide", json=world).status_code == 200

    factions = client.get("/dashboard/api/live").json()["factions"]
    assert [item["id"] for item in factions] == list(range(1, 8))
    assert [item["bases"] for item in factions] == [32, 51, 36, 43, 22, 49, 0]
    assert factions[6]["energy"] == world["metrics"]["energy_reserves"]
    assert factions[0]["energy"] is None


def test_dashboard_page_recreates_the_datalinks_look_without_assets() -> None:
    response = TestClient(create_app(brain=ScriptedBrain())).get("/dashboard")
    assert response.status_code == 200
    assert "PLANETARY DATALINKS" in response.text
    assert "<article class=faction" in response.text
    assert "<div id=factions class=factions>" in response.text
    assert "setInterval(refresh,5000)" in response.text
    assert "<img" not in response.text


def test_missing_world_view_store_keeps_the_known_faction_visible(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "decisions.jsonl")
    source = json.loads((FIXTURES / "thinker_base_production.json").read_text())
    client = TestClient(create_app(brain=ScriptedBrain(), log=log))
    assert client.post("/decide", json=source).status_code == 200

    live = client.get("/dashboard/api/live").json()
    assert live["factions"] == [
        {
            "name": source["faction"],
            "id": None,
            "colour": "#62d6ff",
            "turn": source["turn"],
            "bases": None,
            "population": None,
            "minerals": None,
            "energy": None,
            "income": None,
            "labs": None,
            "military": None,
            "techs": None,
        }
    ]
