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
from neural_amplifier.spend import SPEND_CEILING, SpendLedger


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
