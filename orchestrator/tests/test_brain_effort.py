"""``ClaudeBrain`` must only send an effort level the model actually accepts.

This exists because sending it unconditionally is not a degraded-quality bug, it is a total
outage that hides itself. Haiku 4.5 rejects ``effort`` with a 400; the orchestrator's
degrade-safely path (invariant 9) then returned the native fallback for every decision. The
harness reported "stability 1.00" — five identical fallbacks are perfectly stable — and the run
looked like a well-behaved model until the degrade counter was made loud.

So the property under test is not "we compute the right flag". It is that the failing
combination never reaches ``messages.parse``.

No network: a fake client records what it was called with. That is the whole point — the
regression is in the request we build, so the request is what we assert on.
"""

from __future__ import annotations

from typing import Any

import pytest

from neural_amplifier.brain import BrainError, ClaudeBrain
from neural_amplifier.contract import Action, Orders, WorldView


class _FakeMessages:
    def __init__(self, parsed: Any) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Response:
            stop_reason = "end_turn"
            parsed_output = self.parsed

        return _Response()


class _FakeModels:
    """``models.retrieve`` with the real capability shape, nested two deep.

    Mirrors the live response — a coarse ``effort.supported`` beside a per-level flag — because
    the coarse flag alone is not sufficient: Opus 4.5 reports ``supported: true`` and still
    rejects ``max``.
    """

    def __init__(self, levels: dict[str, bool] | None, fail: bool = False) -> None:
        self.levels = levels
        self.fail = fail
        self.retrieved: list[str] = []

    def retrieve(self, model: str) -> Any:
        self.retrieved.append(model)
        if self.fail:
            raise RuntimeError("capability probe unavailable")
        levels = self.levels or {}
        effort = {k: {"supported": v} for k, v in levels.items()}
        effort["supported"] = any(levels.values())  # type: ignore[assignment]
        return type("_Model", (), {"capabilities": {"effort": effort}})()


class _FakeClient:
    def __init__(self, models: _FakeModels, parsed: Any) -> None:
        self.models = models
        self.messages = _FakeMessages(parsed)


@pytest.fixture
def world_view() -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=35,
        faction="Gaia's Landing",
        surface_id="base.production",
        action_space=[Action(id="unit:0", action="Scout Patrol")],
    )


def _run(monkeypatch: pytest.MonkeyPatch, brain: ClaudeBrain, wv: WorldView, client: Any) -> Any:
    """Drive ``decide`` against a fake client.

    ``decide`` constructs ``anthropic.Anthropic()`` itself rather than taking a client, so the
    seam is the module. Patching it here keeps that constructor injection-free for callers, who
    should not have to know the SDK exists.
    """
    import sys
    import types

    module = types.ModuleType("anthropic")
    module.Anthropic = lambda *a, **k: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return brain.decide(wv)


ORDERS = Orders(choices=[])


def test_effort_omitted_when_the_model_rejects_it(
    monkeypatch: pytest.MonkeyPatch, world_view: WorldView
) -> None:
    """The Haiku 4.5 case. No ``output_config`` at all, rather than a default level."""
    client = _FakeClient(_FakeModels({"high": False, "low": False}), ORDERS)
    brain = ClaudeBrain(model="claude-haiku-4-5-20251001", effort="high")

    _run(monkeypatch, brain, world_view, client)

    (call,) = client.messages.calls
    assert "output_config" not in call
    # Omitted entirely, not sent empty: an empty output_config is a different request, and the
    # point is to send the request we would have sent before effort existed.
    assert call["model"] == "claude-haiku-4-5-20251001"


def test_effort_sent_when_the_model_supports_that_level(
    monkeypatch: pytest.MonkeyPatch, world_view: WorldView
) -> None:
    client = _FakeClient(_FakeModels({"high": True, "max": True}), ORDERS)

    _run(
        monkeypatch, brain := ClaudeBrain(model="claude-opus-5", effort="high"), world_view, client
    )

    assert brain.effort == "high"
    (call,) = client.messages.calls
    assert call["output_config"] == {"effort": "high"}


def test_unsupported_level_on_a_model_that_supports_effort(
    monkeypatch: pytest.MonkeyPatch, world_view: WorldView
) -> None:
    """Opus 4.5 takes low/medium/high and rejects max.

    The case a coarse ``effort.supported`` check gets wrong, which is why the probe reads the
    per-level flag.
    """
    client = _FakeClient(_FakeModels({"high": True, "max": False}), ORDERS)

    _run(monkeypatch, ClaudeBrain(model="claude-opus-4-5", effort="max"), world_view, client)

    assert "output_config" not in client.messages.calls[0]


def test_effort_none_skips_the_probe_entirely(
    monkeypatch: pytest.MonkeyPatch, world_view: WorldView
) -> None:
    """``effort=None`` is an explicit "don't ask", so it must not cost a round trip."""
    client = _FakeClient(_FakeModels({"high": True}), ORDERS)

    _run(monkeypatch, ClaudeBrain(model="claude-opus-5", effort=None), world_view, client)

    assert client.models.retrieved == []
    assert "output_config" not in client.messages.calls[0]


def test_a_failed_probe_omits_effort_rather_than_failing_the_decision(
    monkeypatch: pytest.MonkeyPatch, world_view: WorldView
) -> None:
    """A capability probe is not on the critical path of a turn.

    Losing effort costs decision quality; raising would cost the decision, and the fallback is
    strictly worse than a default-effort answer.
    """
    client = _FakeClient(_FakeModels(None, fail=True), ORDERS)

    _run(monkeypatch, ClaudeBrain(model="claude-opus-5", effort="high"), world_view, client)

    assert "output_config" not in client.messages.calls[0]


def test_the_probe_is_cached_across_decisions(
    monkeypatch: pytest.MonkeyPatch, world_view: WorldView
) -> None:
    """One retrieve per brain, not one per decision.

    ``base.production`` fires per base per turn, so a probe on every decision would put a second
    round trip in front of every one of them to answer a question whose answer cannot change.
    """
    client = _FakeClient(_FakeModels({"high": True}), ORDERS)
    brain = ClaudeBrain(model="claude-opus-5", effort="high")

    _run(monkeypatch, brain, world_view, client)
    _run(monkeypatch, brain, world_view, client)

    assert client.models.retrieved == ["claude-opus-5"]
    assert len(client.messages.calls) == 2
    assert all(c["output_config"] == {"effort": "high"} for c in client.messages.calls)


def test_a_refusal_is_still_a_brain_error(
    monkeypatch: pytest.MonkeyPatch, world_view: WorldView
) -> None:
    """Guards the gating change against swallowing the failure paths around it."""
    client = _FakeClient(_FakeModels({"high": True}), ORDERS)

    def _refuse(**kwargs: Any) -> Any:
        return type("_R", (), {"stop_reason": "refusal", "parsed_output": None})()

    client.messages.parse = _refuse  # type: ignore[method-assign]

    with pytest.raises(BrainError):
        _run(monkeypatch, ClaudeBrain(effort="high"), world_view, client)
