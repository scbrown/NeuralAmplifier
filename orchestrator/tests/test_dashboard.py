from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

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
    assert live["active"] is True
    assert live["updated_at"]
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
    assert "OFFERED ACTION SPACE" in response.text
    assert "PLAN DIRECTIVES" in response.text
    assert "DISAGREEMENT" in response.text
    assert "setTimeout(refresh,delay)" in response.text
    assert "IDLE SINCE" in response.text
    assert "SPEND USD" in response.text
    assert "function renderEvals" in response.text
    assert "Baseline<th>Arm<th>Delta" in response.text
    assert "refresh();loadEvals();" in response.text
    assert "<img" not in response.text


def _inline_script(text: str) -> str:
    match = re.search(r"<script>(.*)</script>", text, re.S)
    assert match, "the page has no inline script"
    return match.group(1)


def test_the_page_script_actually_parses() -> None:
    """na-uq1: every substring assertion above passed while the page rendered NOTHING.

    `DASHBOARD_HTML` is a plain triple-quoted string, so a `\\n` written for JavaScript is
    consumed by PYTHON and emitted as a real newline. A JS regex literal and a JS string
    literal cannot span lines, so `renderEvals`'s `split(/\\n(?==== )/)` became a SyntaxError —
    and a SyntaxError anywhere in an inline script means the WHOLE script never executes, so
    `refresh()` was never defined and never ran. Measured in a real browser:
    `Invalid regular expression: missing /`, with the API serving turn 62 and 430 decisions.

    The author already knew about the double-escape — `\\\\S` and `\\\\d` on the same line are
    correct. Three `\\n` were missed, and no test could see it, because presence is not
    validity: `"function renderEvals" in response.text` is true of a script that cannot run.
    """
    client = TestClient(create_app(brain=ScriptedBrain(), log=DecisionLog(Path(os.devnull))))
    script = _inline_script(client.get("/dashboard").text)

    # Node-free, and specific to the defect: these three must survive as the two characters
    # backslash-n, not as a line break.
    assert r"split(/\n(?==== )/)" in script
    assert r"section.split('\n')" in script
    assert r".join('\n').trim()" in script

    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI without node still gets the assertions above
        pytest.skip("node not available for a real syntax check")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        done = subprocess.run([node, "--check", path], capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert done.returncode == 0, f"the served script does not parse:\n{done.stderr}"


def test_the_status_banner_is_addressed_by_a_binding_and_not_by_a_bare_id() -> None:
    """na-uq1: `status` is not the element — `window.status` is a legacy STRING property.

    Every other id on this page (summary, factions, decisions, evals, detail, detailText) is
    reachable as a bare global; `status` alone collides with a built-in, so
    `status.textContent = ...` assigns to a throwaway String wrapper and is silently discarded.
    Measured in a real browser: `(0,eval)('status')` returns `""`, and `bare === el` is false.

    The banner could therefore never leave "LINKING…" — and the catch block's
    `'LINK DEGRADED // ' + e` could never be displayed either, so the page had no channel to
    report its own failure. That is why the SyntaxError above was invisible in the UI and read
    to a human as "the dashboard shows no data".
    """
    client = TestClient(create_app(brain=ScriptedBrain(), log=DecisionLog(Path(os.devnull))))
    script = _inline_script(client.get("/dashboard").text)

    assert "const statusEl=document.getElementById('status')" in script
    assert re.search(r"(?<![A-Za-z])status\.textContent", script) is None
    assert script.count("statusEl.textContent") == 3


def test_dashboard_publishes_the_real_game_directive_report() -> None:
    result = TestClient(create_app(brain=ScriptedBrain())).get("/dashboard/api/evals").json()

    assert result["ok"] is True
    assert any(run["id"] == "na-mmp" for run in result["runs"])
    assert "hold-reserve-floor" in result["tables"]
    assert "723 decisions with a plan block" in result["tables"]


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


def test_quiet_log_is_reported_as_idle(tmp_path: Path) -> None:
    log_path = tmp_path / "decisions.jsonl"
    client = TestClient(create_app(brain=ScriptedBrain(), log=DecisionLog(log_path)))
    source = json.loads((FIXTURES / "thinker_base_production.json").read_text())
    assert client.post("/decide", json=source).status_code == 200
    os.utime(log_path, (1, 1))

    live = client.get("/dashboard/api/live").json()
    assert live["active"] is False
    assert live["idle_seconds"] > 15
