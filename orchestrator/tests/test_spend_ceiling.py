"""The LLM spend ceiling (``spend.py``): budget_usd is enforced, not just displayed.

A fake priced brain stands in for a paid model: it answers legally and reports a fixed
``cost_usd`` per call, so every assertion here is about the guard, never about a model.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from neural_amplifier import claude_code_brain
from neural_amplifier.claude_code_brain import ClaudeCodeBrain
from neural_amplifier.config import ConfigError, Seat, load
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.dashboard import DashboardReader, run_state
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.seats import SeatRouter
from neural_amplifier.spend import SPEND_CEILING, SpendLedger, price_usage


class PricedBrain:
    """A paid brain that costs ``price`` per call and always answers the first legal action."""

    name = "priced"
    paid = True

    def __init__(self, price: float = 0.01) -> None:
        self.price = price
        self.calls = 0

    def decide(self, world_view: WorldView) -> Orders:
        self.calls += 1
        orders = Orders(choices=[Choice(action_id=world_view.action_space[0].id, reason="paid")])
        orders.cost_usd = self.price
        return orders


def _as(world_view: WorldView, faction_id: int) -> WorldView:
    return world_view.model_copy(update={"faction_id": faction_id})


def test_a_001_budget_stops_after_the_first_priced_call(
    thinker_base: WorldView, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    brain = PricedBrain(0.01)
    log = DecisionLog(tmp_path / "d.jsonl")
    orch = Orchestrator(brain, log=log, spend=SpendLedger(default_budget=0.01))

    with caplog.at_level(logging.WARNING, logger="neural_amplifier.spend"):
        results = [orch.decide(thinker_base) for _ in range(4)]

    assert brain.calls == 1
    first, *rest = [r.record for r in results]
    assert first.tier == "llm" and not first.degraded and first.cost_usd == 0.01
    for record in rest:
        assert record.tier == "fallback"
        assert record.degraded is True
        assert record.degrade_reason and record.degrade_reason.startswith(SPEND_CEILING)
        assert record.cost_usd is None
    # Native answer: empty orders hand the decision back to the engine's own choice.
    assert all(r.orders.choices == [] and r.orders.degraded for r in results[1:])
    # Never silent, and never noisy: exactly one WARNING when it trips.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and SPEND_CEILING in warnings[0].getMessage()
    # The decision log is the meter: per-decision cost lands on the record of truth.
    on_disk = list(log.read())
    assert [r.cost_usd for r in on_disk] == [0.01, None, None, None]


def test_under_budget_it_keeps_calling(thinker_base: WorldView) -> None:
    brain = PricedBrain(0.01)
    orch = Orchestrator(brain, spend=SpendLedger(default_budget=1.0))
    records = [orch.decide(thinker_base).record for _ in range(5)]
    assert brain.calls == 5
    assert all(r.tier == "llm" and not r.degraded for r in records)
    assert orch.spend is not None and orch.spend.spent(orch.game_id) == pytest.approx(0.05)


@pytest.mark.parametrize("spend", [None, SpendLedger()], ids=["no-ledger", "unset-budget"])
def test_unset_budget_is_unchanged(thinker_base: WorldView, spend: SpendLedger | None) -> None:
    brain = PricedBrain(100.0)
    orch = Orchestrator(brain, spend=spend)
    records = [orch.decide(thinker_base).record for _ in range(3)]
    assert brain.calls == 3
    assert all(r.tier == "llm" and not r.degraded for r in records)


def test_an_unpaid_brain_is_never_metered(thinker_base: WorldView) -> None:
    """A zero budget must not degrade an agent/scripted seat that costs nothing per call."""
    from neural_amplifier.brain import ScriptedBrain

    orch = Orchestrator(ScriptedBrain(), spend=SpendLedger(default_budget=0.0))
    assert not orch.decide(thinker_base).record.degraded


def test_seat_mode_one_seat_over_budget_does_not_stop_another(thinker_base: WorldView) -> None:
    seats = {
        1: Seat(1, "alice", "claude-code", budget_usd=0.01),
        2: Seat(2, "bob", "claude-code", budget_usd=1.0),
    }
    drivers = {1: PricedBrain(0.01), 2: PricedBrain(0.01)}
    router = SeatRouter(seats, build=lambda seat: drivers[seat.faction_id])
    ledger = SpendLedger(seat_budgets={f: s.budget_usd for f, s in seats.items()})
    orch = Orchestrator(router, spend=ledger)

    for _ in range(3):
        one = orch.decide(_as(thinker_base, 1)).record
        two = orch.decide(_as(thinker_base, 2)).record
        assert two.tier == "llm" and not two.degraded and two.seat == 2

    assert drivers[1].calls == 1 and drivers[2].calls == 3
    assert one.tier == "fallback" and one.seat == 1
    assert one.degrade_reason and one.degrade_reason.startswith(f"{SPEND_CEILING}: seat 1")
    assert ledger.spent(orch.game_id, 1) == pytest.approx(0.01)
    assert ledger.spent(orch.game_id, 2) == pytest.approx(0.03)


def test_the_meter_is_the_log_summed_per_game_and_survives_a_restart(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    first = Orchestrator(PricedBrain(0.01), log=log, game_id="g1", spend=SpendLedger(0.01))
    first.decide(thinker_base)

    # A restarted service seeds its ledger from the log on disk: the same game stays stopped...
    brain = PricedBrain(0.01)
    again = SpendLedger(0.01).load(log.read())
    record = Orchestrator(brain, log=log, game_id="g1", spend=again).decide(thinker_base).record
    assert brain.calls == 0 and record.tier == "fallback"

    # ...and a different game_id starts its own meter at zero.
    other = Orchestrator(brain, game_id="g2", spend=SpendLedger(0.01).load(log.read()))
    assert other.decide(thinker_base).record.tier == "llm"


def test_a_repair_re_ask_is_also_guarded(thinker_base: WorldView) -> None:
    """The first, rejected answer was paid for; a re-ask over the ceiling must not be made."""

    class IllegalThenLegal(PricedBrain):
        def decide(self, world_view: WorldView) -> Orders:
            self.calls += 1
            orders = Orders(choices=[Choice(action_id="not-offered")])
            orders.cost_usd = self.price
            return orders

    brain = IllegalThenLegal(0.01)
    orch = Orchestrator(brain, spend=SpendLedger(0.01), repair_attempts=2)
    record = orch.decide(thinker_base).record
    assert brain.calls == 1
    assert record.tier == "fallback" and record.degraded and record.cost_usd == 0.01
    assert record.degrade_reason and record.degrade_reason.startswith(SPEND_CEILING)
    # A ceiling stop is not a model failure: the reason must not blame failed repairs.
    assert "repair attempt" not in record.degrade_reason


def _claude_reply(world_view: WorldView, cost: float) -> Any:
    reply = json.dumps({"choices": [{"action_id": world_view.action_space[0].id}]})
    envelope = {"is_error": False, "result": reply, "total_cost_usd": cost}
    return lambda argv, **_: subprocess.CompletedProcess(argv, 0, json.dumps(envelope), "")


def test_claude_code_writes_its_per_call_cost_onto_the_record(
    monkeypatch: pytest.MonkeyPatch, thinker_base: WorldView
) -> None:
    monkeypatch.setattr(claude_code_brain.subprocess, "run", _claude_reply(thinker_base, 0.0387))
    brain = ClaudeCodeBrain()
    orch = Orchestrator(brain, spend=SpendLedger(0.05))
    assert orch.decide(thinker_base).record.cost_usd == 0.0387
    assert orch.decide(thinker_base).record.cost_usd == 0.0387
    assert orch.decide(thinker_base).record.tier == "fallback"
    assert brain.cost_usd == pytest.approx(0.0774)


def test_budget_config_env_then_run_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "na.toml"
    toml.write_text("[run]\nbudget_usd = 2.5\n")
    monkeypatch.delenv("NA_BUDGET_USD", raising=False)
    assert load(toml).run.budget_usd == 2.5
    monkeypatch.setenv("NA_BUDGET_USD", "0.25")
    assert load(toml).run.budget_usd == 0.25
    monkeypatch.delenv("NA_BUDGET_USD")
    assert load(tmp_path / "absent.toml").run.budget_usd is None
    monkeypatch.setenv("NA_BUDGET_USD", "lots")
    with pytest.raises(ConfigError):
        load(toml)
    monkeypatch.setenv("NA_BUDGET_USD", "-1")
    with pytest.raises(ConfigError):
        load(toml)


def test_the_dashboard_names_the_ceiling(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    orch = Orchestrator(PricedBrain(0.01), log=log, spend=SpendLedger(0.01))
    orch.decide(thinker_base)
    orch.decide(thinker_base)

    reader = DashboardReader(log, None, None, query_game_state=False)
    live = reader.live()
    assert live["spend"] == 0.01
    assert live["spend_ceiling"] and live["spend_ceiling"][0]["reason"].startswith(SPEND_CEILING)
    state = run_state({**live, "active": True})
    assert "SPEND CEILING" in state["headline"] and SPEND_CEILING in state["detail"]
    newest = reader.decisions()[0]
    assert newest["tier"] == "fallback" and newest["degrade_reason"].startswith(SPEND_CEILING)


def test_a_restarted_service_keeps_the_game_and_its_meter(
    thinker_base: WorldView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through create_app, as the service is really built: no game_id is passed in."""
    from fastapi.testclient import TestClient

    from neural_amplifier.service import create_app

    monkeypatch.setenv("NA_BUDGET_USD", "0.01")
    monkeypatch.delenv("NA_GAME_ID", raising=False)
    path = tmp_path / "d.jsonl"

    first_brain = PricedBrain(0.01)
    first = TestClient(create_app(brain=first_brain, log=DecisionLog(path), sinks=[]))
    game = first.app.state.orchestrator.game_id
    first.app.state.orchestrator.decide(thinker_base)
    assert first_brain.calls == 1

    # The restart: a new process, a new app, the same log on disk.
    brain = PricedBrain(0.01)
    again = TestClient(create_app(brain=brain, log=DecisionLog(path), sinks=[]))
    assert again.app.state.orchestrator.game_id == game
    record = again.app.state.orchestrator.decide(thinker_base).record
    assert brain.calls == 0
    assert record.tier == "fallback" and record.degraded
    assert record.degrade_reason and record.degrade_reason.startswith(SPEND_CEILING)
    spend = again.get("/health").json()["spend"]
    assert spend[0]["game_id"] == game and spend[0]["tripped"] is True


def test_a_configured_game_id_wins_and_a_fresh_log_mints_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from neural_amplifier.service import create_app

    monkeypatch.setenv("NA_GAME_ID", "game-fixed")
    app = create_app(brain=PricedBrain(), log=DecisionLog(tmp_path / "a.jsonl"), sinks=[])
    assert TestClient(app).app.state.orchestrator.game_id == "game-fixed"

    monkeypatch.delenv("NA_GAME_ID")
    app = create_app(brain=PricedBrain(), log=DecisionLog(tmp_path / "b.jsonl"), sinks=[])
    assert TestClient(app).app.state.orchestrator.game_id.startswith("game-")


def test_the_api_lane_is_priced_from_its_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NA_BRAIN_PRICE", raising=False)
    usage = {"input": 1_000_000, "output": 100_000, "cached": 1_000_000, "cache_write": 0}
    # Haiku 4.5: $1 in, $5 out, cache read 0.1x input.
    assert price_usage("claude-haiku-4-5", usage) == pytest.approx(1.0 + 0.5 + 0.1)
    assert price_usage("claude-haiku-4-5-20251001", usage) == pytest.approx(1.6)
    # Unknown model: the dearest listed rate, never free.
    assert price_usage("some-future-model", {"input": 1_000_000}) == pytest.approx(15.0)
    assert price_usage("claude-haiku-4-5", None) is None
    monkeypatch.setenv("NA_BRAIN_PRICE", "2,4")
    assert price_usage("claude-haiku-4-5", {"input": 1_000_000, "output": 1_000_000}) == (
        pytest.approx(6.0)
    )


def test_the_api_lane_trips_the_ceiling(
    thinker_base: WorldView, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ClaudeBrain records a priced cost_usd, so its paid calls stop at the ceiling too."""
    import sys
    import types

    from neural_amplifier.brain import ClaudeBrain
    from neural_amplifier.response import model_for

    monkeypatch.delenv("NA_BRAIN_PRICE", raising=False)
    parsed = model_for(thinker_base.scope).model_validate(
        {"choices": [{"action_id": thinker_base.action_space[0].id, "reason": "api"}]}
    )
    calls: list[int] = []

    class Messages:
        def parse(self, **_: Any) -> Any:
            calls.append(1)
            usage = types.SimpleNamespace(
                input_tokens=10_000,
                output_tokens=1_000,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            )
            return types.SimpleNamespace(stop_reason="end_turn", parsed_output=parsed, usage=usage)

    class Client:
        messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=lambda: Client()))
    brain = ClaudeBrain(model="claude-haiku-4-5", effort=None)
    orch = Orchestrator(brain, spend=SpendLedger(default_budget=0.01))
    first = orch.decide(thinker_base).record
    # 10k in at $1/M + 1k out at $5/M = $0.015 >= $0.01.
    assert first.cost_usd == pytest.approx(0.015)
    second = orch.decide(thinker_base).record
    assert len(calls) == 1
    assert second.tier == "fallback" and second.degraded


@pytest.mark.parametrize(
    ("model", "rates"),
    [
        # Opus 4 and 4.1 (dated or not) are $15/$75; only Opus 4.5+ is $5/$25.
        ("claude-opus-4-0", (15.0, 75.0)),
        ("claude-opus-4-1", (15.0, 75.0)),
        ("claude-opus-4-20250514", (15.0, 75.0)),
        ("claude-opus-4-1-20250805", (15.0, 75.0)),
        ("claude-opus-4-5", (5.0, 25.0)),
        ("claude-opus-4-5-20251101", (5.0, 25.0)),
        ("claude-opus-4-8", (5.0, 25.0)),
        ("claude-opus-5-5", (4.0, 20.0)),
        ("claude-sonnet-4-6", (3.0, 15.0)),
        ("claude-haiku-5-5", (0.10, 0.50)),
    ],
)
def test_each_model_is_priced_at_its_published_rate(
    monkeypatch: pytest.MonkeyPatch, model: str, rates: tuple[float, float]
) -> None:
    monkeypatch.delenv("NA_BRAIN_PRICE", raising=False)
    usage = {"input": 1_000_000, "output": 1_000_000}
    # 1M prompt tokens is past Haiku 5.5's 100K tier; price that row on a small prompt.
    if model == "claude-haiku-5-5":
        usage = {"input": 50_000, "output": 50_000}
        assert price_usage(model, usage) == pytest.approx(sum(rates) * 0.05)
    else:
        assert price_usage(model, usage) == pytest.approx(sum(rates))


def test_haiku_5_5_long_prompts_cost_the_higher_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NA_BRAIN_PRICE", raising=False)
    # Above 100K prompt tokens (cache reads count toward the prompt): $0.50 in, $2.50 out.
    usage = {"input": 60_000, "cached": 60_000, "output": 10_000}
    expected = (60_000 * 0.50 + 60_000 * 0.50 * 0.1 + 10_000 * 2.50) / 1_000_000
    assert price_usage("claude-haiku-5-5", usage) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["cheap", "1", "1,2,3", "nan,1", "1,inf", "-1,2", "1,-2"])
def test_a_malformed_brain_price_is_refused_at_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("NA_BRAIN_PRICE", raw)
    with pytest.raises(ConfigError, match="NA_BRAIN_PRICE"):
        load(tmp_path / "absent.toml")


def test_a_well_formed_brain_price_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NA_BRAIN_PRICE", "2.5, 12")
    load(tmp_path / "absent.toml")
    assert price_usage("anything", {"input": 1_000_000}) == pytest.approx(2.5)
