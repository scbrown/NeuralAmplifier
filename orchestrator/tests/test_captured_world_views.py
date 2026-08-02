"""World views captured from a real game, parsed as the contract.

Every other fixture in this suite is hand-written, and that is the gap this closes. A
hand-written fixture is written to match the contract, so it cannot catch an adapter that
does not — which is precisely how `history` shipped on both sides, wired to nothing, and
passed every test (na-wzw). These files came out of `WorldViewStore` during real play and
are byte-for-byte what the orchestrator handed the brain.

So the assertions here are deliberately about *arrival*, not about values. Whether Morgan
Solutions had 27 minerals banked is a fact about one turn of one game and will never
recur; whether the adapter populated the typed fields the orchestrator reads by name is a
fact about the seam, and it is the one that keeps breaking.

Refresh them by running a game with `NA_WORLD_VIEW_STORE` set and copying the newest
capture per surface (na-oh5). They are not golden files — nothing here compares against a
stored expectation, so a newer capture can simply replace an older one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_amplifier.contract import WorldView

CAPTURED = Path(__file__).parent / "fixtures" / "captured"


def captures() -> list[tuple[str, WorldView]]:
    out = []
    for path in sorted(CAPTURED.glob("*.json")):
        out.append((path.name, WorldView.model_validate(json.loads(path.read_text()))))
    return out


@pytest.fixture(scope="module")
def base_production() -> WorldView:
    path = CAPTURED / "base_production_turn42.json"
    return WorldView.model_validate(json.loads(path.read_text()))


def test_every_capture_parses_as_the_contract() -> None:
    """The cheapest possible guard, and the one with the best record. An adapter field that
    changes shape shows up here as a ValidationError rather than as a quiet degrade three
    weeks later."""
    parsed = captures()
    assert parsed, f"no captures in {CAPTURED} — see the module docstring to refresh them"
    for name, view in parsed:
        assert view.engine == "thinker", name
        assert view.surface_id, name


def test_the_adapter_populates_the_fields_the_orchestrator_reads_by_name(
    base_production: WorldView,
) -> None:
    """The na-wzw regression, generalised.

    Three fields are read by name on the orchestrator side — `history` for continuity,
    `run_id` to retire a dead game's pendings, `metrics` for directive measurement. Each one
    was, at some point, present in the payload and absent from the parsed object because the
    adapter emitted it under another name or not at all. Asserting the PARSED attribute is
    the whole point: `model_dump()["history"]` is satisfied by a passthrough that leaves
    `world_view.history` empty.
    """
    assert base_production.history is not None, "history (na-wzw)"
    assert base_production.metrics, "metrics (na-b4v)"
    assert base_production.model_dump().get("run_id"), "run_id (na-bzd)"


def test_a_real_action_space_is_decidable(base_production: WorldView) -> None:
    """A bare list of names is not a decidable comparison (decision-inputs.md §1.1), and this
    is the only test that checks it against what an engine actually sent rather than against
    what we remembered to write into a fixture."""
    assert len(base_production.action_space) > 1
    for action in base_production.action_space:
        extra = action.model_dump()
        assert extra.get("cost") is not None, action.id
        # Either estimate is acceptable; `turns_if_continued` appears only on the item already
        # in production, which is exactly one entry.
        assert "turns_if_switched" in extra, action.id


def test_captured_history_uses_the_action_space_vocabulary(base_production: WorldView) -> None:
    """History is only usable if a past choice can be matched against an option on offer
    exactly. On a fresh run history is legitimately empty — this asserts the convention holds
    when there IS one, rather than asserting a game state that will not recur."""
    for entry in base_production.history or []:
        kind, _, num = entry.item.partition(":")
        assert kind in {"unit", "facility"}, entry.item
        assert num.isdigit(), entry.item


def test_fairness_is_recorded_on_a_real_decision(base_production: WorldView) -> None:
    """Invariant 6: never report a result without the handicap profile it was won under. A
    capture with no `fairness` block would mean a real run producing uninterpretable results."""
    assert base_production.fairness is not None
    assert base_production.fairness.slot in {"ai", "human"}
