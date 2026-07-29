"""Fog gating for the foreign-diplomacy feed — ``docs/headless-harness.md`` §4.2."""

from __future__ import annotations

from pathlib import Path

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import WorldView
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.fog import redact
from neural_amplifier.orchestrator import Orchestrator

PACT = {"kind": "treaty", "parties": ["HIVE", "UNIVERSITY"], "text": "pact signed"}
PUBLIC = {"kind": "council", "text": "Planetary Council convened"}
OURS = {"kind": "treaty", "parties": ["GAIANS", "HIVE"], "text": "we signed a truce"}


def view(**kwargs: object) -> WorldView:
    base: dict[str, object] = {
        "engine": "thinker",
        "scope": "turn",
        "turn": 42,
        "faction": "GAIANS",
    }
    return WorldView.model_validate({**base, **kwargs})


def test_a_pact_between_strangers_is_removed() -> None:
    """Thinker's foreign_treaty_popup broadcasts every treaty change. Knowing
    about one between factions we have never met is an information cheat."""
    result = redact(view(contacts=["HIVE"], deltas=[PACT]))
    assert result.removed == 1


def test_our_own_dealings_survive() -> None:
    """The viewing faction is implicitly contacted — it was there."""
    result = redact(view(contacts=["HIVE"], deltas=[OURS]))
    assert result.removed == 0
    assert result.world_view.deltas == [OURS]


def test_public_news_is_not_redacted() -> None:
    """This gate hides private pacts; it must not blind the brain to events
    that name no parties at all."""
    result = redact(view(contacts=[], deltas=[PUBLIC]))
    assert result.removed == 0


def test_a_partially_known_pact_is_still_removed() -> None:
    """Contact with one signatory does not entitle us to the other's business."""
    result = redact(view(contacts=["HIVE"], deltas=[PACT]))
    assert result.world_view.deltas == []


def test_the_brain_never_sees_the_leaked_delta(tmp_path: Path) -> None:
    """The gate runs before the brain call, not after — a redaction applied
    downstream of the prompt would be theatre."""
    seen: list[WorldView] = []

    class Watching(ScriptedBrain):
        def decide(self, world_view: WorldView):  # type: ignore[no-untyped-def]
            seen.append(world_view)
            return super().decide(world_view)

    Orchestrator(Watching()).decide(view(contacts=["HIVE"], deltas=[PACT, OURS]))
    assert seen[0].deltas == [OURS]


def test_redactions_are_counted_on_the_record(tmp_path: Path) -> None:
    """A leaking adapter is otherwise invisible — the run just looks unusually
    well-informed."""
    log = DecisionLog(tmp_path / "d.jsonl")
    result = Orchestrator(ScriptedBrain(), log=log).decide(
        view(contacts=["HIVE"], deltas=[PACT, PACT, OURS])
    )
    assert result.record.redacted_deltas == 2
    assert result.record.fog_enforced is True


def test_an_adapter_that_reports_no_contacts_cannot_be_gated() -> None:
    """Nothing is removed — we cannot tell a legitimate delta from a leaked
    one — but the run must not be reported as fog-clean."""
    result = redact(view(deltas=[PACT]))
    assert result.enforced is False
    assert result.removed == 0
    assert result.world_view.deltas == [PACT]


def test_ungated_runs_are_visible_on_the_record(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    result = Orchestrator(ScriptedBrain(), log=log).decide(view(deltas=[PACT]))
    assert result.record.fog_enforced is False


def test_a_world_view_with_no_deltas_is_untouched() -> None:
    original = view(contacts=["HIVE"])
    result = redact(original)
    assert result.world_view is original
    assert result.enforced is True


def test_malformed_parties_do_not_crash_the_gate() -> None:
    """Adapters grow faster than this file; a bad shape must not stall the
    game. Unparseable parties read as 'no parties', i.e. public."""
    for bad in ({"parties": "HIVE"}, {"parties": None}, {"parties": [1, 2]}, {}):
        assert redact(view(contacts=[], deltas=[bad])).removed == 0
