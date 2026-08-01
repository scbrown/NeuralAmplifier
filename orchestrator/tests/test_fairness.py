"""The fairness ledger.

These tests pin findings read out of the Thinker fork, several of which
contradict the static table in ``docs/game-surface.md`` §5. If one fails after
a fork bump, the ledger is stale — check the cited ``file:line`` before
changing the expectation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neural_amplifier import fairness
from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Fairness, Handicap, WorldView
from neural_amplifier.coverage import report
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.fairness import Config, drift, profile
from neural_amplifier.orchestrator import Orchestrator


def ids(f: Fairness) -> set[str]:
    return {h.id for h in f.handicaps}


def by_id(f: Fairness, handicap_id: str) -> Handicap:
    return next(h for h in f.handicaps if h.id == handicap_id)


# --- the property the whole fairness argument rests on ----------------------


def test_a_human_slot_has_an_empty_ledger() -> None:
    """Every asymmetry in the fork is an is_human branch, so Mode B+ produces
    an empty block at *every* difficulty. This is the only configuration that
    backs an unqualified fair-play claim — assert it at the extreme."""
    for difficulty in fairness.DIFFICULTIES:
        assert profile("human", difficulty).handicaps == []


def test_an_ai_slot_is_never_empty_at_any_difficulty() -> None:
    """The mirror: there is no difficulty at which an AI slot plays clean, so
    a Mode A result is never an unqualified fair-play result."""
    for difficulty in fairness.DIFFICULTIES:
        assert profile("ai", difficulty).handicaps, difficulty


# --- entries the doc table gets wrong --------------------------------------


def test_tech_cost_favours_the_human_at_low_difficulty() -> None:
    """tech_cost_factor is {124,116,108,100,84,76} (main.h:327) — the AI pays
    *more* below Thinker. The doc's flat 'favours: AI' is wrong."""
    assert by_id(profile("ai", "citizen"), "tech_cost_factor").favours == "other"
    assert by_id(profile("ai", "transcend"), "tech_cost_factor").favours == "self"


def test_tech_cost_disappears_where_the_factor_is_neutral() -> None:
    """At Librarian the factor is exactly 100 — declaring a handicap there
    would overstate the ledger."""
    assert "tech_cost_factor" not in ids(profile("ai", "librarian"))


def test_combat_modifiers_favour_the_human() -> None:
    """veh_combat.cpp:1557 scales a low-difficulty human's offense *up* and an
    AI attacker's *down*. The doc table has this one backwards."""
    assert by_id(profile("ai", "citizen"), "combat_modifiers").favours == "other"
    assert "combat_modifiers" not in ids(profile("ai", "transcend"))


def test_content_pop_crosses_over_at_librarian() -> None:
    """content_pop_player {6,5,4,3,2,1} vs _computer {3,3,3,3,3,3}: the human
    is ahead early, level at Librarian, behind at Transcend."""
    assert by_id(profile("ai", "citizen"), "content_pop").favours == "other"
    assert "content_pop" not in ids(profile("ai", "librarian"))
    assert by_id(profile("ai", "transcend"), "content_pop").favours == "self"


def test_unit_support_bonus_is_inert_under_shipped_defaults() -> None:
    """unit_support_bonus ships all-zero (main.h:330). Listing it as an active
    AI advantage would be declaring a handicap nobody has."""
    for difficulty in fairness.DIFFICULTIES:
        assert "unit_support_bonus" not in ids(profile("ai", difficulty))

    configured = Config(unit_support_bonus=(0, 0, 0, 0, 2, 4))
    assert "unit_support_bonus" in ids(profile("ai", "transcend", configured))


def test_retool_penalty_vanishes_when_the_rule_is_disabled() -> None:
    """The branch is `retool_penalty_prod_change && is_human` — zero means
    nobody pays, so there is no asymmetry left to record."""
    assert "retool_penalty" in ids(profile("ai", "transcend"))
    assert "retool_penalty" not in ids(
        profile("ai", "transcend", Config(retool_penalty_prod_change=0))
    )


# --- difficulty thresholds -------------------------------------------------


@pytest.mark.parametrize(
    ("handicap_id", "active", "inactive"),
    [
        ("facility_maint_discount", "thinker", "librarian"),  # *DiffLevel >= DIFF_THINKER
        ("terraform_speed", "thinker", "librarian"),  # *DiffLevel > 3
        ("mind_control_cost", "thinker", "librarian"),  # tgt->diff_level > 3
        ("colony_pod_disband", "talent", "specialist"),  # plr->diff_level > 1
    ],
)
def test_difficulty_thresholds(handicap_id: str, active: str, inactive: str) -> None:
    assert handicap_id in ids(profile("ai", active))
    assert handicap_id not in ids(profile("ai", inactive))


def test_global_warming_exemption_is_lost_at_high_difficulty() -> None:
    """Inverted against the others: the AI's exemption *ends* at Thinker
    (base.cpp:3205), so a harder game is not uniformly more favourable."""
    assert "global_warming" in ids(profile("ai", "librarian"))
    assert "global_warming" not in ids(profile("ai", "thinker"))


def test_project_race_block_favours_the_human() -> None:
    """The ledger is not uniformly tilted — recording it is what shows that."""
    assert by_id(profile("ai", "transcend"), "project_race_block").favours == "other"


# --- classification --------------------------------------------------------


def test_the_two_categories_stay_separated() -> None:
    """Only the structural set needs defending; conflating them is the whole
    reason selected_by exists."""
    at_transcend = profile("ai", "transcend")
    selected = {h.id for h in at_transcend.handicaps if h.selected_by == "difficulty"}
    structural = {h.id for h in at_transcend.structural()}
    assert selected and structural
    assert selected.isdisjoint(structural)
    assert selected | structural == ids(at_transcend)


def test_an_unrecognised_handicap_is_treated_as_structural() -> None:
    """Fail toward needing a defence, not away from it."""
    assert fairness.is_structural("something_a_fork_added") is True
    assert fairness.is_structural("tech_cost_factor") is False


def test_an_unknown_difficulty_raises_rather_than_defaulting() -> None:
    """Silently scoring a typo as Citizen would understate the ledger."""
    with pytest.raises(ValueError, match="unknown difficulty"):
        profile("ai", "transcendent")


def test_every_ledger_entry_cites_a_source() -> None:
    for rule in fairness.LEDGER:
        assert ":" in rule.source, rule.id
    assert len(fairness.BY_ID) == len(fairness.LEDGER), "duplicate ledger id"


# --- drift detection -------------------------------------------------------


def test_a_correct_stamp_shows_no_drift() -> None:
    assert drift(profile("ai", "transcend")).clean


def test_an_adapter_that_stops_stamping_is_caught() -> None:
    """The fairness equivalent of the all-fallback run: the game still plays
    and every result reads as clean."""
    silent = Fairness(slot="ai", difficulty="transcend", handicaps=[])
    result = drift(silent)
    assert not result.clean
    assert "retool_penalty" in result.missing


def test_a_stale_stamp_is_caught() -> None:
    """An adapter still declaring a handicap the rules no longer produce."""
    stale = profile("ai", "librarian")
    stale.handicaps.append(
        Handicap(id="tech_cost_factor", favours="self", selected_by="difficulty")
    )
    assert "tech_cost_factor" in drift(stale).unexpected


def test_a_mislabelled_favours_is_caught() -> None:
    """The exact mistake the doc table made — recording the advantage but
    attributing it to the wrong side."""
    wrong = profile("ai", "citizen")
    by_id(wrong, "combat_modifiers").favours = "self"
    assert "combat_modifiers" in drift(wrong).mislabelled


def test_an_uncheckable_block_is_not_silently_accepted() -> None:
    """No slot or difficulty means nothing can be verified — say so rather
    than reporting clean."""
    assert drift(None).clean
    headless = Fairness(handicaps=[Handicap(id="retool_penalty")])
    assert "retool_penalty" in drift(headless).unexpected


# --- the run-level claim ---------------------------------------------------


def test_a_run_on_an_ai_slot_cannot_claim_unqualified_fair_play(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    view = thinker_base.model_copy(update={"fairness": profile("ai", "transcend")})
    Orchestrator(ScriptedBrain(), log=log).decide(view)

    result = report(log.read())
    assert result.fair_play is False
    assert "retool_penalty" in result.structural_handicaps
    assert "tech_cost_factor" not in result.structural_handicaps


def test_a_mode_b_plus_run_claims_it_cleanly(thinker_base: WorldView, tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    view = thinker_base.model_copy(update={"fairness": profile("human", "transcend")})
    Orchestrator(ScriptedBrain(), log=log).decide(view)

    result = report(log.read())
    assert result.fair_play is True
    assert result.summary()["structural_handicaps"] == []


# --- the adapter reports inputs; the ledger is derived (na-04z) ---------------


def test_an_ai_slot_never_records_an_empty_ledger(thinker_base: WorldView) -> None:
    """An empty `fairness_profile` is the claim "won under unmodified rules".

    An adapter knows only what the engine tells it — which slot, which difficulty. If that were
    recorded as-is, every AI-slot decision would assert fair play on a game that had none, and
    the assertion would be invisible: nothing distinguishes "no handicaps in force" from "nobody
    filled the list in".
    """
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.contract import Fairness
    from neural_amplifier.orchestrator import Orchestrator

    stamped_inputs_only = thinker_base.model_copy(
        update={"fairness": Fairness(slot="ai", difficulty="transcend")}
    )
    record = Orchestrator(ScriptedBrain()).decide(stamped_inputs_only).record

    assert record.fairness_profile, "an AI slot at transcend has handicaps; none were recorded"
    assert "tech_cost_factor" in record.fairness_profile


def test_a_human_slot_derives_an_empty_ledger_and_that_is_the_point(
    thinker_base: WorldView,
) -> None:
    """Mode B+. Every asymmetry in the fork is an `is_human` branch, so a human slot genuinely
    has none — and that emptiness is what backs an unqualified fair-play claim."""
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.contract import Fairness
    from neural_amplifier.orchestrator import Orchestrator

    human = thinker_base.model_copy(
        update={"fairness": Fairness(slot="human", difficulty="transcend")}
    )
    assert Orchestrator(ScriptedBrain()).decide(human).record.fairness_profile == []


def test_an_adapter_that_stamps_its_own_entries_is_left_alone(thinker_base: WorldView) -> None:
    """Derivation fills a gap; it does not overrule an adapter that has an opinion.

    A different engine may know asymmetries this ledger does not model, and silently replacing
    them with the Thinker-derived set would be worse than the empty list it is fixing.
    `fairness.drift` is what checks a stamped block, not this.
    """
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.contract import Fairness, Handicap
    from neural_amplifier.orchestrator import Orchestrator

    own = thinker_base.model_copy(
        update={
            "fairness": Fairness(
                slot="ai",
                difficulty="transcend",
                handicaps=[Handicap(id="engine_specific_thing", favours="self")],
            )
        }
    )
    assert Orchestrator(ScriptedBrain()).decide(own).record.fairness_profile == [
        "engine_specific_thing"
    ]
