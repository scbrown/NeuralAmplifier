"""An LLM run is refused at start if retrieval cannot ground a decision — aegis-09m.

MEASURED before this gate: 0 of 8,607 live LLM decisions across four recorded games carried a
retrieved fact. Each game was blind a different way, and every one served to the end:

- every retrieval call degraded (Haiku M1/M2 and ladder lad1, `quipu_degraded` on 100%);
- no retriever was configured at all (ladder attempt 4, `quipu_absent` on all 1,817);
- reproduced on current main: a dataset the server does not hold. quipu 0.14.0 answers a query
  scoped to an unregistered graph or dataset with ZERO rows and no error, so the shipped
  `na.toml` against a store without the dataset grounded nothing and was not even degraded.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import WorldView
from neural_amplifier.datalinks.quipu import QuipuRetriever
from neural_amplifier.doorbell import Doorbell
from neural_amplifier.knowledge import retrieve
from neural_amplifier.service import BlindStartRefused, create_app

ROW = {
    "label": "Scout Patrol",
    "f": "urn:x:scout",
    "role": "Explore",
    "tier": "canonical",
    "src": "s",
}


def _agent() -> AgentBrain:
    return AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=1)


def _store(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]] | Exception) -> list[str]:
    calls: list[str] = []

    def query(self: QuipuRetriever, sparql: str) -> list[dict[str, Any]]:
        calls.append(sparql)
        if isinstance(rows, Exception):
            raise rows
        return rows

    monkeypatch.setattr(QuipuRetriever, "query", query)
    monkeypatch.setenv("NA_QUIPU_URL", "http://q")
    monkeypatch.setenv("NA_QUIPU_DATASET", "urn:na:dataset")
    return calls


def test_an_llm_run_with_no_retriever_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NA_QUIPU_URL", raising=False)
    with pytest.raises(BlindStartRefused, match="no retriever configured"):
        create_app(brain=_agent())


def test_a_dataset_that_answers_zero_rows_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The silent case: no error anywhere, and nothing to ground with."""
    _store(monkeypatch, [])
    with pytest.raises(BlindStartRefused, match="0 rows from urn:na:dataset"):
        create_app(brain=_agent())


def test_an_unreachable_store_is_refused_with_its_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch, ConnectionRefusedError("[Errno 111] Connection refused"))
    with pytest.raises(BlindStartRefused, match="probe query failed: ConnectionRefusedError"):
        create_app(brain=_agent())


def test_a_grounded_store_starts_and_health_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _store(monkeypatch, [ROW])
    client = TestClient(create_app(brain=_agent()))
    knowledge = client.get("/health").json()["knowledge"]
    assert knowledge == {"state": "grounded", "brain": "agent", "probe_rows": 1}
    assert len(calls) == 1, "exactly one positive-control read at start"


def test_an_explicitly_blind_run_starts_but_never_reads_as_grounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store(monkeypatch, [])
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    knowledge = TestClient(create_app(brain=_agent())).get("/health").json()["knowledge"]
    assert knowledge["state"] == "blind-allowed"
    assert "0 rows" in knowledge["reason"]


def test_the_scripted_brain_is_not_gated_and_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _store(monkeypatch, [])
    knowledge = TestClient(create_app(brain=ScriptedBrain())).get("/health").json()["knowledge"]
    assert knowledge == {"state": "not-required", "brain": "scripted"}
    assert calls == []


def test_a_zero_row_prime_degrades_the_decision_and_records_why(thinker_base: WorldView) -> None:
    """Mid-game, the same silent zero becomes a degraded decision that names its cause."""
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    retriever.query = lambda sparql: []  # type: ignore[method-assign]
    # `faction_id` is an adapter extra, not a declared field: the shape the real adapter sends.
    view = WorldView.model_validate({**thinker_base.model_dump(mode="json"), "faction_id": 1})
    faction = 1
    retriever.prime_turn(view.turn, faction)
    grounding = retrieve(retriever, view)
    assert grounding.degraded
    assert "returned 0 rows" in grounding.reason


def test_the_record_says_why_retrieval_degraded(thinker_base: WorldView) -> None:
    """The boolean alone is what 8,607 records carried; the cause now rides with it."""
    from neural_amplifier.orchestrator import Orchestrator

    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    retriever.query = lambda sparql: []  # type: ignore[method-assign]
    view = WorldView.model_validate({**thinker_base.model_dump(mode="json"), "faction_id": 1})
    retriever.prime_turn(view.turn, 1)
    record = Orchestrator(ScriptedBrain(), retriever=retriever).decide(view).record  # type: ignore[arg-type]
    assert record.knowledge.quipu_degraded
    assert record.knowledge.quipu_reason is not None
    assert "urn:na:dataset returned 0 rows" in record.knowledge.quipu_reason
