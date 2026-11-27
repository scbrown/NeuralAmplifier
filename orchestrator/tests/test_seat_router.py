"""The seat router (aegis-8fye.3, slice B): one brain per faction's chair."""

from __future__ import annotations

import pytest

from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.brain import BrainError, ClaudeBrain, ScriptedBrain
from neural_amplifier.claude_code_brain import ClaudeCodeBrain
from neural_amplifier.config import Brain, Config, Knowledge, Run, Seat
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.seats import SeatRouter, build_reflex
from neural_amplifier.service import build_brain


def _as(world_view: WorldView, faction_id: int | None) -> WorldView:
    return world_view.model_copy(update={"faction_id": faction_id})


def _scripted(action_id: str) -> ScriptedBrain:
    return ScriptedBrain(chooser=lambda wv: Orders(choices=[Choice(action_id=action_id)]))


SEATS = {
    1: Seat(1, "alice", "scripted"),
    2: Seat(2, "bob", "scripted"),
}


def _router() -> tuple[SeatRouter, dict[int, ScriptedBrain]]:
    drivers = {1: _scripted("a1"), 2: _scripted("a2")}
    return SeatRouter(SEATS, build=lambda seat: drivers[seat.faction_id]), drivers


def test_each_seat_answers_with_its_own_driver(thinker_base: WorldView) -> None:
    router, drivers = _router()
    orch = Orchestrator(router)

    one = orch.decide(_as(thinker_base, 1))
    two = orch.decide(_as(thinker_base, 2))

    assert [c.action_id for c in one.orders.choices] == ["a1"]
    assert [c.action_id for c in two.orders.choices] == ["a2"]
    assert [wv.faction_id for wv in drivers[1].calls] == [1]
    assert [wv.faction_id for wv in drivers[2].calls] == [2]
    assert (one.record.seat, one.record.seat_principal, one.record.degraded) == (1, "alice", False)
    assert (two.record.seat, two.record.seat_principal, two.record.degraded) == (2, "bob", False)
    assert one.record.model == "scripted"


def test_unseated_faction_raises_and_degrades(thinker_base: WorldView) -> None:
    router, drivers = _router()

    with pytest.raises(BrainError, match="faction 4 is unseated"):
        router.decide(_as(thinker_base, 4))

    result = Orchestrator(router).decide(_as(thinker_base, 4))
    assert result.record.degraded is True
    # The safe fallback, exactly as any other BrainError gets (test_orchestrator.py).
    assert result.orders.degraded is True
    assert [c.action_id for c in result.orders.choices] == ["a4"]
    assert "faction 4 is unseated" in (result.record.degrade_reason or "")
    assert (result.record.seat, result.record.seat_principal) == (None, None)
    assert drivers[1].calls == [] and drivers[2].calls == []


def test_missing_faction_id_raises(thinker_base: WorldView) -> None:
    router, drivers = _router()
    absent = _as(thinker_base, None)

    with pytest.raises(BrainError, match="no faction_id"):
        router.decide(absent)
    result = Orchestrator(router).decide(absent)
    assert result.record.degraded is True
    assert drivers[1].calls == [] and drivers[2].calls == []


def test_faction_id_is_a_typed_lenient_field(thinker_base: WorldView) -> None:
    raw = thinker_base.model_dump(mode="json")
    raw.pop("faction_id", None)
    assert WorldView.model_validate(raw).faction_id is None
    assert WorldView.model_validate({**raw, "faction_id": 3}).faction_id == 3
    # Same answer `_extra` gave for a wrong-typed value: absent, not a rejected world view.
    assert WorldView.model_validate({**raw, "faction_id": "3"}).faction_id is None


def _config(kind: str = "scripted", model: str | None = None, seats=None) -> Config:
    return Config(
        brain=Brain(kind=kind, model=model, effort=None),
        knowledge=Knowledge(),
        run=Run(),
        seats=seats or {},
    )


def test_seats_make_build_brain_a_router() -> None:
    brain = build_brain(_config(seats=SEATS))
    assert isinstance(brain, SeatRouter)
    assert brain.name == "seats"
    assert set(brain.drivers) == {1, 2}


@pytest.mark.parametrize(
    ("kind", "model", "cls", "attrs"),
    [
        ("scripted", None, ScriptedBrain, {}),
        ("claude", None, ClaudeBrain, {}),
        ("claude", "claude-x", ClaudeBrain, {"model": "claude-x"}),
        ("claude-code", "m", ClaudeCodeBrain, {"model": "m"}),
        ("agent", None, AgentBrain, {}),
    ],
)
def test_no_seats_build_brain_is_unchanged(kind, model, cls, attrs) -> None:
    brain = build_brain(_config(kind, model))
    assert type(brain) is cls
    assert brain.name == cls.name
    for key, value in attrs.items():
        assert getattr(brain, key) == value


def test_build_reflex_uses_the_existing_constructors() -> None:
    assert type(build_reflex(Seat(1, "p", "scripted"))) is ScriptedBrain
    claude = build_reflex(Seat(1, "p", "claude", model="claude-x"))
    assert type(claude) is ClaudeBrain and claude.model == "claude-x"
    code = build_reflex(Seat(1, "p", "claude-code", model="m"))
    assert type(code) is ClaudeCodeBrain and code.model == "m"
    assert type(build_reflex(Seat(1, "p", "agent"))) is AgentBrain
