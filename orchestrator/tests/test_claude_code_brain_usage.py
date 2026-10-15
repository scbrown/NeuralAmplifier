"""The claude-code lane records which model answered and what it cost — aegis-5xi.

MEASURED 2026-10-15 on main 29e698f: one real decision through
`Orchestrator(ClaudeCodeBrain()).decide` recorded `model="claude-code"` and tokens 0/0/0 while
the brain itself counted $0.2125 for the call. Ladder attempt 4 has the same shape on 1,817 of
1,817 decisions. The `claude -p` envelope carried both answers the whole time (`usage`,
`modelUsage`); nothing read them.

The envelope below is the shape measured from the real CLI the same day
(`claude -p --output-format json --model claude-opus-5-5`).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from neural_amplifier import claude_code_brain
from neural_amplifier.claude_code_brain import ClaudeCodeBrain, _served_model, _usage_of_envelope
from neural_amplifier.contract import WorldView
from neural_amplifier.orchestrator import Orchestrator


def _envelope(result: str, **extra: Any) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "is_error": False,
        "result": result,
        "total_cost_usd": 0.0387,
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 4424,
            "cache_read_input_tokens": 0,
            "output_tokens": 165,
        },
        "modelUsage": {
            "claude-opus-5-5": {"inputTokens": 2, "outputTokens": 165, "costUSD": 0.0387},
        },
    }
    envelope.update(extra)
    return envelope


def test_usage_keeps_provider_semantics_and_counts_cache_writes() -> None:
    usage = _usage_of_envelope(_envelope("{}"))
    assert usage == {"input": 2, "output": 165, "cached": 0, "cache_write": 4424}


def test_missing_or_malformed_usage_is_absent_not_zero() -> None:
    """Zeroes would read as a measured free call: the exact defect being fixed."""
    assert _usage_of_envelope({"usage": None}) is None
    assert _usage_of_envelope({}) is None
    assert _usage_of_envelope({"usage": {"input_tokens": "2"}}) is None


def test_the_answering_model_is_the_one_that_produced_the_output() -> None:
    helper_and_main = {
        "modelUsage": {
            "claude-haiku-4-5": {"outputTokens": 12},
            "claude-opus-5-5": {"outputTokens": 165},
        }
    }
    assert _served_model(helper_and_main) == "claude-opus-5-5"
    assert _served_model(_envelope("{}")) == "claude-opus-5-5"
    assert _served_model({"modelUsage": {}}) is None
    assert _served_model({}) is None


def test_a_decided_turn_records_the_served_model_and_real_tokens(
    monkeypatch: pytest.MonkeyPatch, thinker_base: WorldView
) -> None:
    """End to end through validation, which REBUILDS Orders and once dropped `usage` there."""
    legal = thinker_base.action_space[0].id
    reply = json.dumps({"choices": [{"action_id": legal, "reason": "fixture"}]})

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess:
        assert "--model" not in argv, "the lane stays unpinned; only the record learns the model"
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(_envelope(reply)), stderr="")

    monkeypatch.setattr(claude_code_brain.subprocess, "run", fake_run)
    result = Orchestrator(ClaudeCodeBrain()).decide(thinker_base)

    record = result.record
    assert record.tier == "llm" and not record.degraded
    assert record.model == "claude-opus-5-5"
    assert (record.tokens.input, record.tokens.output, record.tokens.cached) == (2, 165, 0)
    assert record.tokens.model_dump()["cache_write"] == 4424


def test_a_pinned_model_is_still_overridden_by_what_actually_answered(
    monkeypatch: pytest.MonkeyPatch, thinker_base: WorldView
) -> None:
    """An alias or a fallback can serve a different model than requested; record the server's."""
    legal = thinker_base.action_space[0].id
    reply = json.dumps({"choices": [{"action_id": legal, "reason": "fixture"}]})
    monkeypatch.setattr(
        claude_code_brain.subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 0, json.dumps(_envelope(reply)), ""),
    )
    record = Orchestrator(ClaudeCodeBrain(model="opus")).decide(thinker_base).record
    assert record.model == "claude-opus-5-5"
