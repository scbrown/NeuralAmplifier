"""Coverage as the assertion surface, not a report nobody reads."""

from __future__ import annotations

from pathlib import Path

from neural_amplifier import surfaces
from neural_amplifier.brain import BrainError, ScriptedBrain
from neural_amplifier.contract import WorldView
from neural_amplifier.coverage import report
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.orchestrator import Orchestrator


def test_surface_ids_are_frozen_and_partitioned() -> None:
    """The three domains must not overlap — scope_for() would be ambiguous."""
    assert surfaces.BASE.isdisjoint(surfaces.UNIT)
    assert surfaces.BASE.isdisjoint(surfaces.FACTION)
    assert surfaces.UNIT.isdisjoint(surfaces.FACTION)
    assert surfaces.ALL == surfaces.BASE | surfaces.UNIT | surfaces.FACTION


def test_gap_list_is_a_subset_of_the_registry() -> None:
    """A dialog-only surface still has to be a known surface."""
    assert surfaces.NO_AI_PATH <= surfaces.ALL


def test_scope_matches_the_contract_vocabulary() -> None:
    assert surfaces.scope_for("base.production") == "base"
    assert surfaces.scope_for("unit.design") == "unit"
    assert surfaces.scope_for("faction.tech") == "turn"
    assert surfaces.scope_for("not.a.surface") is None


def test_coverage_counts_what_fired(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    orchestrator = Orchestrator(ScriptedBrain(), log=log)
    orchestrator.decide(thinker_base)
    orchestrator.decide(thinker_base)

    result = report(log.read())
    assert result.total == 2
    assert result.fired["base.production"] == 2
    assert "base.production" in result.covered()


def test_uncovered_surfaces_are_visible(thinker_base: WorldView, tmp_path: Path) -> None:
    """A surface implemented but never fired means the scenario is wrong or the
    hook is misplaced — invisible without this."""
    log = DecisionLog(tmp_path / "d.jsonl")
    Orchestrator(ScriptedBrain(), log=log).decide(thinker_base)

    result = report(log.read())
    assert "unit.design" in result.uncovered()
    assert "base.production" not in result.uncovered()


def test_a_fully_degraded_run_is_distinguishable_from_a_real_one(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """The failure tests miss: every turn fell back, the run completed, and
    every 'did it run?' assertion passes. degrade_rate is what catches it."""
    log = DecisionLog(tmp_path / "d.jsonl")
    orchestrator = Orchestrator(ScriptedBrain(raises=BrainError("down")), log=log)
    for _ in range(4):
        orchestrator.decide(thinker_base)

    result = report(log.read())
    assert result.total == 4
    assert result.degrade_rate == 1.0
    # ...and it still "played" every turn, which is exactly the trap.
    assert result.fired["base.production"] == 4


def test_healthy_run_has_zero_degradation(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    Orchestrator(ScriptedBrain(), log=log).decide(thinker_base)
    assert report(log.read()).degrade_rate == 0.0


def test_adherence_is_asserted_as_exactly_zero(thinker_base: WorldView, tmp_path: Path) -> None:
    from neural_amplifier.contract import Choice, Orders

    log = DecisionLog(tmp_path / "d.jsonl")
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1"), Choice(action_id="ghost")])])
    Orchestrator(brain, log=log).decide(thinker_base)

    result = report(log.read())
    assert result.adherence_violations == 1
    assert result.adherent is False


def test_unknown_surface_id_is_flagged(thinker_base: WorldView, tmp_path: Path) -> None:
    """An adapter emitting an ID outside the frozen registry is a bug — most
    likely a rename that silently invalidates historical coverage."""
    log = DecisionLog(tmp_path / "d.jsonl")
    typo = thinker_base.model_copy(update={"surface_id": "base.producton"})
    Orchestrator(ScriptedBrain(), log=log).decide(typo)

    assert report(log.read()).unknown_surface_ids == {"base.producton"}


def test_missing_surface_id_is_counted_not_ignored(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    anonymous = thinker_base.model_copy(update={"surface_id": None})
    Orchestrator(ScriptedBrain(), log=log).decide(anonymous)

    result = report(log.read())
    assert result.missing_surface_id == 1
    assert result.summary()["surfaces_fired"] == 0


# --- surface registry coverage ---------------------------------------------


def test_the_remaining_surfaces_partition_exactly() -> None:
    """The buckets must sum to what is left, or planning reads off a false number.

    They did not. `docs/game-surface.md` described the gap as "21 with no AI path", "32
    unit-scope", and "the rest" — which double-counts the seven surfaces that are both, and
    understated the immediately-instrumentable pile as 20 when it was 27.

    A FOURTH bucket, `subsumed`, joined later and this test caught it being added without being
    counted — which is the same class of error as the original, one bucket on. It carries the
    same hazard too: a subsumed surface can also be unit-scope or lack a native path, so it is
    subtracted from the other buckets rather than counted alongside them.
    """
    from neural_amplifier.surfaces import coverage

    c = coverage()
    assert c["observed"] + c["remaining"] == c["total"]
    parts = c["needs_tier_first"] + c["volume_bound"] + c["subsumed"] + c["ready"]
    assert parts == c["remaining"]


def test_applied_is_the_coverage_number_and_never_exceeds_observed() -> None:
    """A surface is not covered until the decision can be *applied*.

    Observing changes what is recorded, not what the game does — the engine's own choice still
    executes — so counting observation as coverage claims influence the brain does not have.
    Applying without observing is incoherent in the other direction: nothing would have produced
    the action space the choice was made from.
    """
    from neural_amplifier.surfaces import APPLIED, OBSERVED, coverage

    assert APPLIED <= OBSERVED
    c = coverage()
    assert c["applied"] <= c["observed"] <= c["total"]
    assert c["observed_not_applied"] == c["observed"] - c["applied"]


def test_every_instrumented_surface_is_in_the_frozen_registry() -> None:
    """A surface id not in the registry cannot be measured against it — and the registry is
    frozen precisely so a rename invalidates recorded runs loudly."""
    from neural_amplifier.surfaces import ALL, OBSERVED

    assert OBSERVED <= ALL


def test_nothing_applied_lacks_a_fallback() -> None:
    """Invariant 9. A surface with no native AI path has nothing to degrade to, so the brain
    must not be allowed to *decide* it before its deterministic tier exists.

    Scoped to APPLIED rather than OBSERVED deliberately: observing a no-AI-path surface is
    harmless and in fact the first step, because the engine still does whatever it did.
    """
    from neural_amplifier.surfaces import APPLIED, NO_AI_PATH

    assert not (APPLIED & NO_AI_PATH)


def test_subsumed_ids_stay_in_the_frozen_registry() -> None:
    """Classifying is not removing.

    Renaming or dropping an id invalidates every previously recorded run, which is why the
    registry is frozen. SUBSUMED changes what `coverage()` counts as work waiting to be done and
    nothing else — every id in it is still a real surface the game has.
    """
    from neural_amplifier.surfaces import ALL, SUBSUMED

    assert SUBSUMED <= ALL


def test_a_surface_is_never_both_observed_and_subsumed() -> None:
    """The two answer different questions and must not blur.

    OBSERVED means a record with an action space exists and the brain can see the decision.
    SUBSUMED means there is no separate decision to see — either another id already covers it,
    or the engine computes rather than chooses. A surface in both would be claiming a decision
    is simultaneously visible and non-existent.
    """
    from neural_amplifier.surfaces import OBSERVED, SUBSUMED

    assert not (OBSERVED & SUBSUMED)


def test_instrumented_without_an_action_space_is_subsumed_not_observed() -> None:
    """base.workers, base.specialists and base.name ARE instrumented — and still not coverage.

    Their records carry no `action_space`, because the contract's is pick-one and neither an
    allocation over 21 tiles nor a name drawn from a data file is that shape. With nothing a
    brain could see and take, putting them in OBSERVED would claim a decision is available when
    none is. This is the distinction that keeps "we record it" and "the brain can decide it"
    from collapsing into one number.
    """
    from neural_amplifier.surfaces import OBSERVED, SUBSUMED

    for surface in ("base.workers", "base.specialists", "base.name"):
        assert surface in SUBSUMED, surface
        assert surface not in OBSERVED, surface
