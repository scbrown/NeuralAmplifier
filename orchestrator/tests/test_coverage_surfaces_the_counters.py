"""What a run's health report is obliged to SHOW — na-bql.

Two gaps, both measured on ladder-attempt4 2026-08-24.

`report()` computed `degraded` and `summary()` never published it, so the only trace of a
fallback in the output was `degrade_rate`, a rounded ratio. A reader who computes the obvious
`llm_decisions / decisions` gets 1.00 for a row with 19 fallbacks and 1.00 for a row with none.
I read it that way myself and reported "coverage all-LLM and clean" from a row that had already
fallen back — twice, while holding all the context. A rate answers "how bad"; only the count
answers "did this happen at all".

And three counters on the brain — `malformed`, `cost_usd`, and `transient_retries` added the
same night — were maintained and read by nothing. `malformed`'s own docstring says it is
"surfaced rather than swallowed", and it was surfaced nowhere. The consequence is not academic:
after a retry landed on the live row a clean decision came back, and there was no way to tell
the retry from the upstream storm easing.

The load-bearing arm here is `test_the_counters_are_read_from_the_LIVE_brain`. Asserting that a
field exists and reads zero would pass against a hardcoded zero, which is the same class of
vacuous check this bead is about.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.coverage import report
from neural_amplifier.decisions import DecisionLog, DecisionRecord
from neural_amplifier.service import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def _record(**kw: Any) -> DecisionRecord:
    base: dict[str, Any] = {
        "turn": 1,
        "faction": "Peacekeepers",
        "engine": "thinker",
        "scope": "base",
        "tier": "llm",
        "world_view_hash": "sha256:test",
        "action_space_size": 3,
        "surface_id": "base.production",
    }
    base.update(kw)
    return DecisionRecord(**base)


def test_the_summary_publishes_the_degraded_COUNT_not_only_the_rate() -> None:
    out = report([_record(degraded=True), _record(), _record()]).summary()
    assert out["degraded"] == 1
    assert out["degrade_rate"] == round(1 / 3, 4)


def test_the_count_can_be_ZERO() -> None:
    """Anti-vacuity: a field that is always 1 would pass the arm above."""
    out = report([_record(), _record()]).summary()
    assert out["degraded"] == 0


def test_the_count_survives_a_run_that_is_ALL_fallback() -> None:
    """The case the rate handles well and the ratio hides completely."""
    out = report([_record(degraded=True), _record(degraded=True)]).summary()
    assert out["degraded"] == 2
    assert out["degrade_rate"] == 1.0
    # and the confusable ratio still reads as full coverage, which is why the count is needed
    assert out["llm_decisions"] == out["decisions"]


def _client(tmp_path: Path, brain: Any) -> tuple[TestClient, Any]:
    log = DecisionLog(tmp_path / "decisions.jsonl")
    return TestClient(create_app(brain=brain, log=log)), brain


def test_coverage_reports_the_brain_counters(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("NA_WORLD_VIEW_STORE", str(tmp_path / "wv"))

    class CountingBrain(ScriptedBrain):
        name = "counting"
        malformed = 3
        transient_retries = 2
        cost_usd = 1.5

    client, _ = _client(tmp_path, CountingBrain())
    body = client.get("/coverage").json()

    assert body["brain"] == {"malformed": 3, "transient_retries": 2, "cost_usd": 1.5}


def test_the_counters_are_read_from_the_LIVE_brain(tmp_path: Path, monkeypatch: Any) -> None:
    """THE arm that matters: a static zero would satisfy every assertion above."""
    monkeypatch.setenv("NA_WORLD_VIEW_STORE", str(tmp_path / "wv"))

    class CountingBrain(ScriptedBrain):
        name = "counting"
        malformed = 0
        transient_retries = 0
        cost_usd = 0.0

    brain = CountingBrain()
    client, _ = _client(tmp_path, brain)

    before = client.get("/coverage").json()["brain"]
    assert before["transient_retries"] == 0

    brain.transient_retries = 7
    brain.malformed = 4
    brain.cost_usd = 0.25

    after = client.get("/coverage").json()["brain"]
    assert after["transient_retries"] == 7, "the endpoint is not reading the live brain"
    assert after["malformed"] == 4
    assert after["cost_usd"] == 0.25


def test_a_brain_without_the_counters_grows_no_empty_fields(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Adding a counter here must never break a brain that does not have it."""
    monkeypatch.setenv("NA_WORLD_VIEW_STORE", str(tmp_path / "wv"))

    class Bare:
        name = "bare"

        def decide(self, world_view: Any) -> Any:  # pragma: no cover - never called
            raise AssertionError("not used")

    client, _ = _client(tmp_path, Bare())
    body = client.get("/coverage").json()

    assert "error" not in body
    assert "brain" not in body, "a brain with no counters must not get an empty block"


def test_a_real_decision_moves_the_published_count(tmp_path: Path, monkeypatch: Any) -> None:
    """End to end through /decide, so the wiring is proven and not only the aggregator."""
    monkeypatch.setenv("NA_WORLD_VIEW_STORE", str(tmp_path / "wv"))
    client, _ = _client(tmp_path, ScriptedBrain())
    world = json.loads((FIXTURES / "thinker_base_production.json").read_text())

    assert client.post("/decide", json=world).status_code == 200
    body = client.get("/coverage").json()

    assert body["decisions"] == 1
    assert body["degraded"] == 0
