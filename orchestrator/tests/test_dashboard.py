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
from neural_amplifier.dashboard import DashboardReader
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
    assert "DIRECTIVES IN FORCE" in response.text
    assert "GROUNDING FACTS" in response.text
    assert "GROUNDING_NOTE" in response.text
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


# ---------------------------------------------------------------------------
# na-cjv slice 1: the why-view. These assert the two distinctions the panel
# exists to preserve, because both failures are silent — an ambiguous panel
# renders perfectly and simply answers a different question than the reader asked.
# ---------------------------------------------------------------------------


def test_an_in_force_directive_carries_what_became_of_it() -> None:
    """`in_force` minus `followed` must not read as "the model ignored it".

    MEASURED on ladder-attempt4: `unsatisfied` is populated on 609 of 610 decisions and the
    panel rendered it on none, so four directives in force with two followed left the other
    two unexplained.
    """
    from neural_amplifier.dashboard import directive_dispositions

    rows = directive_dispositions(
        {
            "in_force": ["expand-20-by-80", "hq-formers-before-pods", "arbitration-court"],
            "followed": ["expand-20-by-80"],
            "unsatisfied": ["hq-formers-before-pods"],
            "overrode": ["arbitration-court"],
        }
    )
    got = {r["id"]: r["dispositions"] for r in rows}
    assert got["expand-20-by-80"] == ["followed"]
    assert got["hq-formers-before-pods"] == ["unsatisfied"]
    assert got["arbitration-court"] == ["overrode"]


def test_a_directive_with_no_recorded_disposition_is_labelled_not_dropped() -> None:
    from neural_amplifier.dashboard import directive_dispositions

    rows = directive_dispositions({"in_force": ["lonely-directive"], "followed": []})
    assert rows == [{"id": "lonely-directive", "dispositions": [], "in_force": True}]


def test_the_three_kinds_of_no_grounding_are_distinguishable() -> None:
    """absent / degraded / empty must never collapse into one blank.

    The degraded case is a FAULT. If it renders the same as "the graph had nothing to say",
    a failed retrieval is reported as a quiet healthy nothing — the exact class this panel
    is built to make visible.
    """
    from neural_amplifier.dashboard import grounding_state

    absent = grounding_state({"quipu_absent": True, "quipu_facts": []}, None)
    degraded = grounding_state({"quipu_degraded": True, "quipu_facts": []}, None)
    empty = grounding_state({"quipu_hits": 0, "quipu_facts": []}, None)

    assert absent["state"] == "absent"
    assert degraded["state"] == "degraded"
    assert empty["state"] == "empty"
    # the labels a reader actually sees must differ too, not just the enum
    assert len({absent["label"], degraded["label"], empty["label"]}) == 3


def test_grounding_marks_which_facts_were_actually_cited() -> None:
    from neural_amplifier.dashboard import grounding_state

    got = grounding_state(
        {
            "quipu_hits": 2,
            "quipu_facts": ["unit:colony-pod expands the base count", "tech:centauri unrelated"],
            "quipu_cited": ["unit:colony-pod"],
        },
        None,
    )
    assert got["state"] == "present"
    assert [f["cited"] for f in got["facts"]] == [True, False]


def test_grounding_falls_back_to_the_world_view_list() -> None:
    """The retriever writes facts into the world view; the knowledge block is a summary."""
    from neural_amplifier.dashboard import grounding_state

    got = grounding_state({"quipu_hits": 1}, {"grounding": ["base:hq holds 3 minerals"]})
    assert [f["text"] for f in got["facts"]] == ["base:hq holds 3 minerals"]


def test_the_decision_endpoint_carries_the_why_block(tmp_path: Path, monkeypatch) -> None:
    """End to end: the API a browser calls must actually ship the block."""
    from neural_amplifier.decisions import DecisionRecord

    log = DecisionLog(tmp_path / "decisions.jsonl")
    log.write(
        DecisionRecord(
            turn=7,
            faction="Peacekeepers",
            engine="thinker",
            scope="base",
            surface_id="base.production",
            tier="llm",
            world_view_hash="h",
            action_space_size=2,
            chosen=[{"action_id": "unit:0", "reason": "expansion is the binding lever"}],
            reason="expansion is the binding lever",
            plan={"in_force": ["expand-20-by-80"], "unsatisfied": ["expand-20-by-80"]},
            knowledge={"quipu_absent": True, "quipu_facts": [], "hank_verdict": "allow"},
        )
    )
    client = TestClient(create_app(brain=ScriptedBrain(), log=log))
    body = client.get("/dashboard/api/decisions/0").json()
    why = body["why"]
    assert why["directives"] == [
        {"id": "expand-20-by-80", "dispositions": ["unsatisfied"], "in_force": True}
    ]
    assert why["grounding"]["state"] == "absent"
    assert why["guard"]["verdict"] == "allow"


def _plan_log(tmp_path: Path) -> DecisionLog:
    from neural_amplifier.decisions import DecisionRecord

    log = DecisionLog(tmp_path / "decisions.jsonl")
    for turn, plan in (
        (1, {"in_force": ["expand", "reserve"], "followed": ["expand"], "unsatisfied": ["expand", "reserve"]}),
        (2, {"in_force": ["expand", "reserve"], "overrode": ["reserve"], "unsatisfied": ["expand"]}),
    ):
        log.write(
            DecisionRecord(
                turn=turn,
                faction="Peacekeepers",
                engine="thinker",
                scope="base",
                tier="llm",
                world_view_hash="h",
                action_space_size=1,
                chosen=[{"action_id": "unit:0"}],
                plan=plan,
            )
        )
    return log


def test_strategy_reports_each_directive_per_turn_with_its_dispositions(tmp_path: Path) -> None:
    reader = DashboardReader(_plan_log(tmp_path), None)
    got = reader.strategy()
    assert [t["turn"] for t in got["turns"]] == [1, 2]
    totals = {t["id"]: t for t in got["totals"]}
    assert totals["expand"]["in_force"] == 2
    assert totals["expand"]["followed"] == 1
    assert totals["expand"]["unsatisfied"] == 2
    assert totals["reserve"]["overrode"] == 1


def test_followed_share_is_over_turns_in_force_not_over_followed_plus_unsatisfied(
    tmp_path: Path,
) -> None:
    """`followed` and `unsatisfied` co-occur, so the naive denominator exceeds the population.

    MEASURED: followed+unsatisfied happens 568 times in ladder-attempt4. Dividing by their sum
    would report a directive that was followed every single turn as roughly half-followed.
    """
    reader = DashboardReader(_plan_log(tmp_path), None)
    totals = {t["id"]: t for t in reader.strategy()["totals"]}
    # expand: in force twice, followed once -> 0.5, NOT 1/(1+2)=0.333
    assert totals["expand"]["followed_share"] == 0.5


def test_missing_plan_file_is_named_not_silently_rendered_as_bare_ids(tmp_path: Path) -> None:
    reader = DashboardReader(_plan_log(tmp_path), None)
    got = reader.strategy(None)
    assert got["definitions_source"] == "unavailable"
    assert got["definitions"] == {}
    # the dispositions are still complete — only the TEXT is missing
    assert got["totals"]


def test_a_plan_file_supplies_the_directive_text(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {"directives": [{"id": "expand", "intent": "grow to 20 bases", "metric": "base_count"}]}
        ),
        encoding="utf-8",
    )
    reader = DashboardReader(_plan_log(tmp_path), None)
    got = reader.strategy(plan)
    assert got["definitions_source"] == "plan-file"
    assert got["definitions"]["expand"]["intent"] == "grow to 20 bases"


def test_an_unreadable_plan_file_is_distinguished_from_an_absent_one(tmp_path: Path) -> None:
    plan = tmp_path / "broken.json"
    plan.write_text("{ not json", encoding="utf-8")
    reader = DashboardReader(_plan_log(tmp_path), None)
    assert reader.strategy(plan)["definitions_source"] == "unreadable"


def test_the_strategy_endpoint_is_served(tmp_path: Path) -> None:
    client = TestClient(create_app(brain=ScriptedBrain(), log=_plan_log(tmp_path)))
    body = client.get("/dashboard/api/strategy").json()
    assert {t["id"] for t in body["totals"]} == {"expand", "reserve"}


def test_a_plan_file_that_defines_only_some_directives_says_how_many(tmp_path: Path) -> None:
    """`source == "plan-file"` is not the claim that every directive has text.

    MEASURED on ladder-attempt4: the plan file defines 1 directive while 8 appear in the log,
    because the rest were issued at runtime. Reporting only the source would leave seven rows
    blank with nothing saying why.
    """
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"directives": [{"id": "expand", "intent": "grow"}]}), encoding="utf-8")
    got = DashboardReader(_plan_log(tmp_path), None).strategy(plan)
    assert got["definitions_source"] == "plan-file"
    assert got["directive_count"] == 2
    assert got["definitions_covered"] == 1
    assert got["definitions_missing"] == ["reserve"]
