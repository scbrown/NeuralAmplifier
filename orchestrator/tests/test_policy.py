"""Per-surface tier policy — `surfaces.toml`, `policy.py`.

Two properties, and the second is the one that would rot quietly.

A surface switched off must still **produce a record**, or it becomes indistinguishable from a
surface nothing ever emitted — and telling those apart is the entire job of coverage.

And it must never be counted as **degraded**. Degraded means the brain was asked and could not
answer; disabled means it was never asked. `degrade_rate` is the number that catches a run where
the brain was silently absent, so a deliberate configuration leaking into it would break the one
metric that must not be able to lie in that direction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.coverage import report
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.policy import PolicyError, SurfacePolicy, load


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "surfaces.toml"
    path.write_text(body)
    return path


def test_an_unknown_surface_id_is_refused_at_load(tmp_path: Path) -> None:
    """A typo'd id would otherwise be a toggle that looks set and does nothing — the failure
    only ever surfacing as "why is the brain still deciding that"."""
    with pytest.raises(PolicyError, match="frozen surface registry"):
        load(write(tmp_path, '[surfaces]\n"base.pruduction" = true\n'))


def test_a_non_boolean_toggle_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="true or false"):
        load(write(tmp_path, '[surfaces]\n"base.production" = "yes"\n'))


def test_an_explicit_false_survives_a_permissive_default(tmp_path: Path) -> None:
    """The bug this nearly shipped with: storing only the enabled ids meant a `default = true`
    policy silently re-enabled everything anyone had deliberately switched off."""
    policy = load(write(tmp_path, 'default = true\n[surfaces]\n"base.production" = false\n'))
    assert policy.allows("base.production") is False
    assert policy.allows("faction.tech") is True


def test_no_file_means_no_opinion(tmp_path: Path) -> None:
    """Absent is not empty. Nothing configured means everything is decided, which is how the
    orchestrator behaved before this file existed; an empty table with `default = false` is
    someone deliberately turning everything off, and must be honoured."""
    assert load(tmp_path / "missing.toml").allows("base.production") is True
    assert load(write(tmp_path, "default = false\n")).allows("base.production") is False


def test_a_disabled_surface_is_recorded_but_never_asked(thinker_base: WorldView) -> None:
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1")])])
    policy = SurfacePolicy(toggles={}, default=False, source=Path("surfaces.toml"))

    result = Orchestrator(brain, policy=policy).decide(thinker_base)

    assert brain.calls == []  # the brain was never consulted
    assert result.record.tier == "deterministic"
    assert result.record.surface_id == thinker_base.surface_id
    assert result.orders.choices == []  # no instruction; the engine does what it always did


def test_a_disabled_surface_is_not_degraded(thinker_base: WorldView) -> None:
    """The distinction the whole module exists for."""
    policy = SurfacePolicy(toggles={}, default=False, source=Path("surfaces.toml"))
    record = Orchestrator(ScriptedBrain(), policy=policy).decide(thinker_base).record

    assert record.degraded is False
    assert record.degrade_reason is None


def test_configuration_cannot_flatter_the_degrade_rate(thinker_base: WorldView) -> None:
    """A run that is mostly switched off must not dilute the failure rate of what it did own.

    One enabled surface that degrades, and several disabled ones. Over `total` this reads as a
    quarter; over the decisions the brain was actually asked for it is what it is — total
    failure on the one surface in play.
    """
    from neural_amplifier.brain import BrainError

    off = SurfacePolicy(toggles={}, default=False, source=Path("surfaces.toml"))
    on = SurfacePolicy(toggles={}, default=True, source=Path("surfaces.toml"))

    broken = Orchestrator(ScriptedBrain(raises=BrainError("timeout")), policy=on)
    quiet = Orchestrator(ScriptedBrain(), policy=off)

    records = [broken.decide(thinker_base).record] + [
        quiet.decide(thinker_base).record for _ in range(3)
    ]
    out = report(records)

    assert out.total == 4
    assert out.llm_decisions == 1
    assert out.deterministic_decisions == 3
    assert out.degrade_rate == 1.0


def test_a_surface_without_an_id_is_still_decided() -> None:
    """The adapter is meant to stamp a surface_id (invariant 5), and a missing one is already
    counted as a coverage gap by `report`. Refusing to decide it here would turn one
    instrumentation bug into a second, larger one: a silently deterministic game.

    So the strictest possible policy — everything off — still lets an unstamped decision
    through, and it is recorded at `llm` tier because the brain really was asked.
    """
    from neural_amplifier.contract import Action

    unstamped = WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="Gaians",
        action_space=[Action(id="a1", action="Recycling Tanks")],
    )
    policy = SurfacePolicy(toggles={}, default=False, source=Path("surfaces.toml"))
    assert policy.allows(None) is True

    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1")])])
    result = Orchestrator(brain, policy=policy).decide(unstamped)

    assert len(brain.calls) == 1
    assert result.record.tier == "llm"
    assert [c.action_id for c in result.orders.choices] == ["a1"]
