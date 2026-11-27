"""The practice loop over captured fixtures: author, trial on every seed, feed back, admit or shelve.

No game runs here. The author is scripted, the trial runner is the offline one over
``fixtures/captured``, and every decision goes through the full orchestrator with the guard.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from neural_amplifier.practice import (
    MAX_ROUNDS,
    Draft,
    Goal,
    OfflineTrialRunner,
    RoundFeedback,
    Seed,
    loop,
)
from neural_amplifier.skill_runner import SkillRunner
from neural_amplifier.skills import LintError, SkillStore, SuccessCheck

#: Colony pods while the base can pay for them, found by role, never by id.
EXPAND = """
def on_decision(view, min_surplus = 2):
    pods = [a for a in view["action_space"] if "NEW BASE" in a.get("role", "")]
    if pods and view["metrics"]["mineral_surplus"] >= min_surplus:
        return choose(sorted(pods, key = lambda a: a["cost"])[0]["id"], "expand")
    return None
"""

#: Reads a key no world view has: a script error on every seed.
BROKEN = """
def on_decision(view):
    return choose(view["colony_pods"][0], "expand")
"""

#: Names an action the engine did not offer: refused by validation.
INVENTED = """
def on_decision(view):
    return choose(view["action_space"][0]["id"] + "_extra", "expand")
"""

#: An order with the wrong arity: refused by the order gate before anything is applied.
GATED = """
def on_decision(view):
    order("skip")
    return choose(view["action_space"][0]["id"], "expand")
"""

#: Always hurries: the guard denies it when reserves cannot pay.
HURRY_ALWAYS = """
def on_decision(view):
    for a in view["action_space"]:
        if a["action"] == "Hurry production":
            return choose(a["id"], "hurry")
    return None
"""

CHECK = {"metric": "base_count", "comparator": "at_least", "target": 1, "horizon_turns": 25}
GOAL = Goal(
    "expand_when_paid",
    "base production: build a colony pod while the base has mineral surplus",
    ("base.production",),
)
VARIANTS = {"lean": {"mineral_surplus": 3}, "rich": {"energy_reserves": 900}}
SEEDS = [
    Seed("base_production_turn42"),
    Seed("base_production_turn42", "lean"),
    Seed("base_production_turn42", "rich"),
    Seed("base_production_turn135"),
]


class ScriptedAuthor:
    """Hands back the next draft in its script, and keeps what it was told."""

    def __init__(self, drafts: Sequence[Draft]) -> None:
        self.drafts = list(drafts)
        self.seen: list[tuple[RoundFeedback, ...]] = []

    def write(self, goal: Goal, history: Sequence[RoundFeedback]) -> Draft:
        self.seen.append(tuple(history))
        return self.drafts[min(len(self.seen), len(self.drafts)) - 1]


@pytest.fixture(scope="module")
def trials() -> OfflineTrialRunner:
    runner = OfflineTrialRunner(runner=SkillRunner(), variants=VARIANTS)
    yield runner
    runner.close()


def test_a_pass_on_round_two_is_admitted_as_verified(trials: OfflineTrialRunner) -> None:
    store = SkillStore()
    author = ScriptedAuthor([Draft(BROKEN, CHECK), Draft(EXPAND, CHECK)])
    result = loop(GOAL, author, trials, SEEDS, store=store)

    assert result.outcome == "admitted"
    assert [r.round for r in result.rounds] == [1, 2]
    assert result.skill is not None and result.skill.status == "verified"
    assert result.skill.version == 2 and result.skill.check == SuccessCheck(**CHECK)
    assert len(result.skill.passing_seeds()) == len(SEEDS)
    assert {t.source for t in result.skill.trials} == {s.source for s in SEEDS}
    # The failing round-1 version keeps its failed trials as evidence.
    v1 = store.get(GOAL.name, 1)
    assert v1 is not None and {t.verdict for t in v1.trials} == {"fail"}
    # Round 2 was written knowing what round 1 did.
    assert len(author.seen[1]) == 1 and author.seen[1][0].trials[0].script_error is not None
    assert store.retrieve(GOAL.description, "base.production", statuses=("verified",))


def test_four_failures_are_shelved_with_the_reason_recorded(trials: OfflineTrialRunner) -> None:
    store = SkillStore()
    drafts = [
        Draft(BROKEN, CHECK),
        Draft(INVENTED, CHECK),
        Draft(GATED, CHECK),
        Draft(BROKEN + "\n", CHECK),
    ]
    author = ScriptedAuthor(drafts)
    result = loop(GOAL, author, trials, SEEDS, store=store)

    assert result.outcome == "shelved"
    assert len(result.rounds) == MAX_ROUNDS and len(author.seen) == MAX_ROUNDS
    assert "shelved after 4 round(s)" in result.reason
    history = store.history(GOAL.name)
    assert [s.status for s in history] == ["retired"] * 4
    assert all(s.retired_reason == result.reason for s in history)
    assert result.skill is not None and result.skill.status == "retired"
    assert store.eligible(("verified", "candidate")) == []


def test_shelved_reason_survives_reopening_the_store(tmp_path, trials: OfflineTrialRunner) -> None:
    store = SkillStore(tmp_path)
    result = loop(GOAL, ScriptedAuthor([Draft(BROKEN, CHECK)]), trials, SEEDS, store=store)
    assert result.outcome == "shelved"
    skill = SkillStore(tmp_path).get(GOAL.name)
    assert skill is not None and skill.status == "retired"
    assert skill.retired_reason == result.reason
    assert skill.check == SuccessCheck(**CHECK)


def test_feedback_carries_the_script_error_refusals_outcomes_and_verdict(
    trials: OfflineTrialRunner,
) -> None:
    failing_check = {**CHECK, "target": 99}
    drafts = [
        Draft(BROKEN, CHECK),
        Draft(INVENTED, CHECK),
        Draft(GATED, CHECK),
        Draft(EXPAND, failing_check),
    ]
    author = ScriptedAuthor(drafts)
    result = loop(GOAL, author, trials, SEEDS, store=SkillStore())
    error, invented, gated, missed = result.rounds

    first = error.trials[0]
    assert first.verdict == "fail" and first.script_error is not None
    assert (
        first.script_error["failure"] == "error" and "colony_pods" in first.script_error["detail"]
    )

    [refusal] = invented.trials[0].refusals
    assert refusal.stage == "validation" and "_extra" in refusal.detail
    assert invented.trials[0].orders[0].outcome == "refused"

    [gate] = gated.trials[0].refusals
    assert gate.stage == "order_gate" and "skip" in gate.detail

    verdict = missed.trials[0].check
    assert verdict is not None and not verdict.passed and verdict.observed == 5.0
    assert "base_count" in verdict.detail
    [pod] = missed.trials[0].orders
    assert (pod.kind, pod.outcome, pod.engine) == ("choose", "accepted", "unknown")
    assert missed.trials[0].deltas == {"minerals_remaining": -33.0}

    # Structured, not prose: every round serialises to plain data the author can read.
    json.dumps([r.as_dict() for r in author.seen[-1]])


def test_the_guard_refusal_reaches_the_author(trials: OfflineTrialRunner) -> None:
    runner = OfflineTrialRunner(runner=trials.runner, variants={"broke": {"energy_reserves": 0}})
    store = SkillStore()
    skill = store.save("hurry", "hurry", HURRY_ALWAYS, check=CHECK)
    report = runner.run(skill, Seed("base_hurry_turn42", "broke"))
    assert report.verdict == "fail"
    assert [r.stage for r in report.refusals] == ["guard"]
    assert report.orders[0].outcome == "refused"
    assert runner.run(skill, Seed("base_hurry_turn42")).verdict == "pass"


def test_a_draft_without_a_check_is_refused_and_fed_back(trials: OfflineTrialRunner) -> None:
    store = SkillStore()
    with pytest.raises(LintError, match="needs a success check"):
        store.save(GOAL.name, GOAL.description, EXPAND, require_check=True)
    author = ScriptedAuthor([Draft(EXPAND, None), Draft(EXPAND, CHECK)])
    result = loop(GOAL, author, trials, SEEDS, store=store)
    assert result.outcome == "admitted"
    first = result.rounds[0]
    assert first.skill is None and first.save_refusal is not None
    assert first.save_refusal.stage == "save" and "success check" in first.save_refusal.detail
    assert result.skill is not None and result.skill.version == 1  # nothing saved in round 1


@pytest.mark.parametrize(
    "check, match",
    [
        ({**CHECK, "metric": "bases"}, "unknown metric 'bases'"),
        ({**CHECK, "comparator": "greater"}, "comparator"),
        ({**CHECK, "horizon_turns": 0}, "horizon_turns"),
        ({**CHECK, "target": float("nan")}, "finite"),
        ({"metric": "base_count", "comparator": "at_least", "target": 1}, "horizon_turns"),
    ],
)
def test_a_check_the_game_cannot_settle_is_refused_at_save(check: dict, match: str) -> None:
    with pytest.raises(LintError, match=match):
        SkillStore().save(GOAL.name, GOAL.description, EXPAND, check=check)


def test_a_bad_metric_name_in_practice_is_a_save_refusal(trials: OfflineTrialRunner) -> None:
    author = ScriptedAuthor([Draft(EXPAND, {**CHECK, "metric": "bases"})])
    result = loop(GOAL, author, trials, SEEDS, store=SkillStore())
    assert result.outcome == "shelved" and result.skill is None
    assert all(r.save_refusal is not None for r in result.rounds)
    assert "unknown metric 'bases'" in result.rounds[0].save_refusal.detail


def test_the_round_cap_holds(trials: OfflineTrialRunner) -> None:
    author = ScriptedAuthor([Draft(BROKEN, CHECK)])
    result = loop(GOAL, author, trials, SEEDS, store=SkillStore())
    assert len(author.seen) == MAX_ROUNDS and len(result.rounds) == MAX_ROUNDS
    assert [len(h) for h in author.seen] == [0, 1, 2, 3]
    short = ScriptedAuthor([Draft(BROKEN, CHECK)])
    assert len(loop(GOAL, short, trials, SEEDS, max_rounds=2).rounds) == 2
    for bad in (0, MAX_ROUNDS + 1):
        with pytest.raises(ValueError, match="max_rounds"):
            loop(GOAL, ScriptedAuthor([Draft(EXPAND, CHECK)]), trials, SEEDS, max_rounds=bad)


def test_admission_needs_enough_distinct_seeds(trials: OfflineTrialRunner) -> None:
    few = [Seed("base_production_turn42"), Seed("base_production_turn42")]
    with pytest.raises(ValueError, match="distinct seed"):
        loop(GOAL, ScriptedAuthor([Draft(EXPAND, CHECK)]), trials, few)


def test_one_failing_seed_keeps_the_candidate_out(trials: OfflineTrialRunner) -> None:
    # Surplus below the threshold on one seed: the skill falls through there, which proves nothing.
    runner = OfflineTrialRunner(runner=trials.runner, variants={"poor": {"mineral_surplus": 0}})
    seeds = [*SEEDS[:1], Seed("base_production_turn42", "poor"), SEEDS[3]]
    store = SkillStore(min_verified_seeds=2)
    result = loop(GOAL, ScriptedAuthor([Draft(EXPAND, CHECK)]), runner, seeds, store=store)
    assert result.outcome == "shelved"
    poor = result.rounds[0].trials[1]
    assert poor.fell_through and poor.verdict == "fail"


def test_seeds_are_distinct_and_unknown_labels_and_metrics_are_refused(
    trials: OfflineTrialRunner,
) -> None:
    assert len({s.number for s in SEEDS}) == len(SEEDS)
    assert trials.seeds(["base_production_turn42"]) == SEEDS[:3]
    with pytest.raises(ValueError, match="unknown seed label"):
        trials.world_view(Seed("base_production_turn42", "nope"))
    with pytest.raises(ValueError, match="unknown metric"):
        OfflineTrialRunner(runner=trials.runner, variants={"x": {"bases": 1}})


def test_a_draft_identical_to_a_retired_version_is_refused_not_promoted(
    trials: OfflineTrialRunner,
) -> None:
    store = SkillStore()
    old = store.save(
        GOAL.name, GOAL.description, EXPAND, surfaces=GOAL.surfaces, check=CHECK, require_check=True
    )
    store.retire(GOAL.name, old.version, "shelved by an earlier run")
    author = ScriptedAuthor([Draft(EXPAND, CHECK), Draft(EXPAND + "\n# revised\n", CHECK)])
    result = loop(GOAL, author, trials, SEEDS, store=store)
    first = result.rounds[0]
    assert first.skill is None and first.save_refusal is not None
    assert "identical to retired" in first.save_refusal.detail
    assert "shelved by an earlier run" in first.save_refusal.detail
    retired = store.get(GOAL.name, old.version)
    assert retired is not None and retired.status == "retired" and retired.trials == ()
    assert result.outcome == "admitted" and result.skill is not None
    assert result.skill.version == old.version + 1
