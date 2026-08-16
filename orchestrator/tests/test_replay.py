"""Replay as regression — ``docs/observability.md`` step 7.

The point of these is that a recorded game becomes a test suite that runs with
no game, no adapter, and no tokens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neural_amplifier.brain import BrainError, ScriptedBrain
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.replay import WorldViewStore, replay, stored


def record_a_run(
    tmp_path: Path, views: list[WorldView], brain: ScriptedBrain | None = None
) -> tuple[DecisionLog, WorldViewStore]:
    log = DecisionLog(tmp_path / "d.jsonl")
    store = WorldViewStore(tmp_path / "views")
    orchestrator = Orchestrator(brain or ScriptedBrain(), log=log, store=store)
    for view in views:
        orchestrator.decide(view)
    return log, store


# --- the gap this closes ---------------------------------------------------


def test_a_log_alone_cannot_be_replayed(thinker_base: WorldView, tmp_path: Path) -> None:
    """The record carries only world_view_hash. Without a store there is
    nothing to feed back, and replay must say so rather than reporting a clean
    run over zero decisions."""
    log = DecisionLog(tmp_path / "d.jsonl")
    Orchestrator(ScriptedBrain(), log=log).decide(thinker_base)

    result = replay(log.read(), WorldViewStore(tmp_path / "empty"), Orchestrator(ScriptedBrain()))
    assert result.total == 1
    assert result.missing_inputs == 1
    assert result.replayed == 0
    assert result.deterministic is False  # nothing was actually verified


def test_the_store_round_trips_by_hash(thinker_base: WorldView, tmp_path: Path) -> None:
    log, store = record_a_run(tmp_path, [thinker_base])
    record = next(iter(log.read()))

    assert store.get(record.world_view_hash) is not None
    assert store.get(record.world_view_hash).model_dump() == thinker_base.model_dump()  # type: ignore[union-attr]


def test_identical_inputs_are_stored_once(thinker_base: WorldView, tmp_path: Path) -> None:
    """Content addressing is what keeps a long game's store small."""
    _, store = record_a_run(tmp_path, [thinker_base, thinker_base, thinker_base])
    assert len(list(store.root.glob("*.json"))) == 1


def test_the_store_holds_the_gated_view_not_the_raw_one(tmp_path: Path) -> None:
    """Replay has to reproduce what the brain saw. Storing the pre-fog view
    would make every replay of a leaky adapter diverge for the wrong reason."""
    leaky = WorldView.model_validate(
        {
            "engine": "thinker",
            "scope": "turn",
            "turn": 1,
            "faction": "GAIANS",
            "contacts": [],
            "deltas": [{"kind": "treaty", "parties": ["HIVE", "UNIVERSITY"]}],
        }
    )
    _, store = record_a_run(tmp_path, [leaky])
    assert next(iter(stored(store))).deltas == []


def test_the_store_refuses_to_be_used_as_a_sink(tmp_path: Path) -> None:
    """It needs the world view, which a record does not carry. emit() swallows
    per-call errors by design, so the mistake has to be caught at wiring time
    or it would fail silently for a whole run."""
    store = WorldViewStore(tmp_path / "views")
    with pytest.raises(TypeError, match="not a Sink"):
        Orchestrator(ScriptedBrain(), sinks=[store])  # type: ignore[list-item]


# --- the regression gate ---------------------------------------------------


def test_an_unchanged_orchestrator_replays_identically(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    log, store = record_a_run(tmp_path, [thinker_base, thinker_base])

    result = replay(log.read(), store, Orchestrator(ScriptedBrain()))
    assert result.replayed == 2
    assert result.matched == 2
    assert result.deterministic is True
    assert result.consistent is True


def test_a_changed_decision_is_reported_with_both_sides(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """The diff has to name what changed, or a red gate is a bisect session."""
    log, store = record_a_run(
        tmp_path, [thinker_base], ScriptedBrain([Orders(choices=[Choice(action_id="a1")])])
    )

    changed = ScriptedBrain([Orders(choices=[Choice(action_id="a2")])])
    result = replay(log.read(), store, Orchestrator(changed))

    assert result.deterministic is False
    divergence = result.diverged[0]
    assert divergence.before == ["a1"]
    assert divergence.after == ["a2"]
    assert divergence.turn == thinker_base.turn
    assert divergence.surface_id == "base.production"


def test_a_newly_degrading_brain_is_caught_even_when_the_action_matches(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """The trap: the fallback happens to pick what the brain picked, every
    'did it decide?' assertion passes, and the brain is gone."""
    end_turn = next(a.id for a in thinker_base.action_space if a.action == "end_turn")
    log, store = record_a_run(
        tmp_path, [thinker_base], ScriptedBrain([Orders(choices=[Choice(action_id=end_turn)])])
    )

    result = replay(log.read(), store, Orchestrator(ScriptedBrain(raises=BrainError("down"))))
    assert result.matched == 1  # the chosen action lines up...
    assert result.new_degradations == 1  # ...and it is still a regression
    assert result.consistent is False


def test_a_lost_surface_shows_up_in_the_summary(
    thinker_base: WorldView, glsmac_thin: WorldView, tmp_path: Path
) -> None:
    log, store = record_a_run(tmp_path, [thinker_base, glsmac_thin])

    result = replay(log.read(), store, Orchestrator(ScriptedBrain()))
    assert result.summary()["surfaces_lost"] == []
    assert result.surfaces_before == result.surfaces_after


def test_stored_views_are_harvestable_as_fixtures(
    thinker_base: WorldView, glsmac_thin: WorldView, tmp_path: Path
) -> None:
    """A real run's inputs are better fixtures than anything hand-written."""
    _, store = record_a_run(tmp_path, [thinker_base, glsmac_thin])
    harvested = list(stored(store))

    assert len(harvested) == 2
    assert {v.engine for v in harvested} == {"thinker", "glsmac"}


# --- repairs -----------------------------------------------------------------


def _repairable() -> tuple[WorldView, ScriptedBrain]:
    """A decision the guard denies once, then accepts — the shape a repair needs.

    `hurry:now` is legal by the engine's list and unaffordable at 40 reserves, so the first
    answer is stripped to nothing and the brain is re-asked with the reason attached.
    """
    from neural_amplifier.contract import Action

    world_view = WorldView(
        engine="thinker",
        scope="base",
        turn=42,
        faction="Gaians",
        surface_id="base.hurry",
        action_space=[
            Action(id="hurry:now", action="Hurry production", effects={"energy_reserves": -81.0}),
            Action(id="hurry:none", action="Do not hurry"),
        ],
        metrics={"energy_reserves": 40},
    )
    brain = ScriptedBrain(
        responses=[
            Orders(choices=[Choice(action_id="hurry:now")]),
            Orders(choices=[Choice(action_id="hurry:none")]),
        ]
    )
    return world_view, brain


def test_the_view_a_repair_was_asked_from_is_stored(tmp_path: Path) -> None:
    """The gap: the store is written once, before the repair loop, so the augmented view the
    brain actually answered from existed for the length of one call and then nothing held the
    bytes. `world_view_hash` addresses the first prompt and cannot address the second.
    """
    from neural_amplifier.hank import StateGuard

    world_view, brain = _repairable()
    store = WorldViewStore(tmp_path / "views")
    record = Orchestrator(brain, store=store, guard=StateGuard()).decide(world_view).record

    assert record.repairs == 1
    assert len(record.repair_inputs) == 1

    reasked = store.get(record.repair_inputs[0])
    assert reasked is not None
    assert reasked.advisories  # the reason the brain was given, recoverable after the fact
    assert record.repair_inputs[0] != record.world_view_hash


def test_a_decision_that_needed_no_repair_records_none(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """The baseline the count is read against — and a check that a clean decision does not pay
    for this in store writes."""
    _, store = record_a_run(tmp_path, [thinker_base])
    record = next(iter(DecisionLog(tmp_path / "d.jsonl").read()))

    assert record.repairs == 0
    assert record.repair_inputs == []
    assert len(list(stored(store))) == 1


def test_the_repair_count_survives_without_a_store(tmp_path: Path) -> None:
    """Hashes need somewhere to point; the count does not. A run with no store configured must
    still be able to say that a decision took two round trips, because that is a turn the game
    spent waiting."""
    from neural_amplifier.hank import StateGuard

    world_view, brain = _repairable()
    record = Orchestrator(brain, guard=StateGuard()).decide(world_view).record

    assert record.repairs == 1
    assert record.repair_inputs == []


def test_replay_still_starts_from_the_original_input(tmp_path: Path) -> None:
    """`world_view_hash` stays the decision's input rather than widening to the last prompt.

    A replay regenerates its own advisories, so a changed guard producing a different second
    prompt shows up as a divergence — which is the entire job — instead of being hidden by
    replaying the recorded prompt back at the brain.
    """
    from neural_amplifier.hank import StateGuard

    world_view, brain = _repairable()
    log = DecisionLog(tmp_path / "d.jsonl")
    store = WorldViewStore(tmp_path / "views")
    Orchestrator(brain, log=log, store=store, guard=StateGuard()).decide(world_view)

    (before,) = list(log.read())
    assert store.get(before.world_view_hash) is not None
    assert store.get(before.world_view_hash).advisories is None
