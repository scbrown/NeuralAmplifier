"""The HTTP seam — the contract as it actually goes over the wire."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from neural_amplifier.brain import BrainError, ScriptedBrain
from neural_amplifier.contract import Choice, Orders
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.service import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_health_reports_the_brain_in_use() -> None:
    client = TestClient(create_app(brain=ScriptedBrain()))
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["brain"] == "scripted"


def test_health_exposes_telemetry_state(tmp_path: Path) -> None:
    """A run whose exporter is quietly failing looks identical to a healthy one
    from the outside — unless /health says so."""
    log = DecisionLog(tmp_path / "d.jsonl")
    client = TestClient(create_app(brain=ScriptedBrain(), log=log, sinks=[]))
    telemetry = client.get("/health").json()["telemetry"]

    assert telemetry["sinks"] == ["DecisionLog"]
    assert telemetry["healthy"] is True


def test_health_surfaces_a_broken_sink() -> None:
    class Broken:
        def write(self, record: object) -> None:
            raise RuntimeError("collector unreachable")

    client = TestClient(create_app(brain=ScriptedBrain(), sinks=[Broken()]))  # type: ignore[list-item]
    client.post("/decide", json=_payload("thinker_base_production"))
    telemetry = client.get("/health").json()["telemetry"]

    assert telemetry["healthy"] is False
    assert "collector unreachable" in telemetry["failures"][0]


def test_decide_returns_orders_from_the_action_space() -> None:
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1", reason="economy first")])])
    client = TestClient(create_app(brain=brain))

    body = client.post("/decide", json=_payload("thinker_base_production")).json()

    assert body["choices"] == [{"action_id": "a1", "reason": "economy first"}]
    assert body["degraded"] is False


def test_decide_marks_degraded_orders_on_the_wire() -> None:
    """The adapter needs to know it applied a fallback, not a decision."""
    client = TestClient(create_app(brain=ScriptedBrain(raises=BrainError("unreachable"))))

    body = client.post("/decide", json=_payload("thinker_base_production")).json()

    assert body["degraded"] is True
    assert body["choices"][0]["action_id"] == "a4"


def test_malformed_world_view_is_rejected() -> None:
    client = TestClient(create_app(brain=ScriptedBrain()))
    assert client.post("/decide", json={"engine": "thinker"}).status_code == 422


def test_coverage_endpoint_summarises_the_run(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    client = TestClient(create_app(brain=ScriptedBrain(), log=log))

    client.post("/decide", json=_payload("thinker_base_production"))
    client.post("/decide", json=_payload("glsmac_turn_thin"))

    summary = client.get("/coverage").json()
    assert summary["decisions"] == 2
    assert summary["surfaces_fired"] == 2
    assert summary["degrade_rate"] == 0.0
    assert summary["adherence_violations"] == 0
