"""Skills over captured world views: SkillBrain, the hermetic runner, the literal-id lint.

Every decision here is a real engine capture (``fixtures/captured``), decided through the full
``Orchestrator.decide`` path, so a skill's answer meets the same validation as a model's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import WorldView
from neural_amplifier.faction_policy import PolicyStore
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.queued import QueueStore
from neural_amplifier.skill_runner import SkillRunner
from neural_amplifier.skills import LintError, SkillBrain, SkillError, SkillStore, lint

CAPTURED = Path(__file__).parent / "fixtures" / "captured"

#: Expand while the base can pay for it: pick the action whose role founds a base, found by its
#: role, never by id. Queues a standing hurry refusal through the view's own base id.
EXPAND = """
def on_decision(view, min_surplus = 2):
    if view["surface_id"] != "base.production":
        return None
    pods = [a for a in view["action_space"] if "NEW BASE" in a.get("role", "")]
    if pods and view["metrics"]["mineral_surplus"] >= min_surplus:
        cheapest = sorted(pods, key = lambda a: a["cost"])[0]
        return choose(cheapest["id"], "expand while the base can pay for it")
    return None
"""

#: Hurry only while reserves are well above the cost; otherwise decline. Reads both action ids
#: from the action space by label.
HURRY = """
def on_decision(view, reserve = 100):
    by_label = {a["action"]: a["id"] for a in view["action_space"]}
    if view["metrics"]["energy_reserves"] >= reserve:
        pick = by_label["Hurry production"]
    else:
        pick = by_label["Do not hurry"]
    queue_answer(
        view["surface_id"], pick,
        [{"metric": "energy_reserves", "comparator": "at_least", "target": reserve}],
        until_turn = view["turn"] + 5, base_id = view["base_id"], reason = "reserves hold",
    )
    return choose(pick, "reserves at or above %d" % reserve)
"""

#: Social engineering: keep the current settings unless an option is labelled for wealth.
SE = """
def on_decision(view):
    for a in view["action_space"]:
        if a["action"] == "Wealth":
            return choose(a["id"], "economy first")
    return None
"""


def captured(name: str) -> WorldView:
    return WorldView.model_validate(json.loads((CAPTURED / f"{name}.json").read_text()))


@pytest.fixture(scope="module")
def runner() -> SkillRunner:
    r = SkillRunner()
    yield r
    r.close()


@pytest.fixture
def store() -> SkillStore:
    s = SkillStore()
    s.save(
        "expand_when_paid",
        "base production: build a colony pod while the base has mineral surplus",
        EXPAND,
        surfaces=["base.production"],
    )
    s.save(
        "hurry_on_reserves",
        "base hurry: spend energy reserves to hurry",
        HURRY,
        surfaces=["base.hurry"],
    )
    s.save("se_wealth", "faction social engineering: choose wealth", SE, surfaces=["faction.se"])
    return s


# --- answers a captured decision legally and identically twice ------------------------------


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("base_production_turn42", "unit:0"),
        ("base_production_turn135", None),  # mineral_surplus 2 → pod chosen by role
        ("base_hurry_turn42", "hurry:now"),
        ("faction_se_turn135", "se:2:3"),
    ],
)
def test_a_stored_skill_answers_a_captured_decision_legally_and_identically_twice(
    store: SkillStore, runner: SkillRunner, fixture: str, expected: str | None
) -> None:
    wv = captured(fixture)
    fallback = ScriptedBrain()
    brain = SkillBrain(store, fallback, runner=runner, practice=True, queue=QueueStore())
    first = Orchestrator(brain).decide(wv)
    first_skill = brain.last_skill
    second = Orchestrator(brain).decide(wv)

    assert fallback.calls == []
    assert first_skill is not None and brain.last_skill == first_skill
    assert not first.orders.degraded
    assert first.orders.model_dump() == second.orders.model_dump()
    [choice] = first.orders.choices
    assert choice.action_id in wv.action_ids()
    if expected is not None:
        assert choice.action_id == expected
    assert choice.reason is not None and first_skill.ref in choice.reason


def test_effects_are_converted_through_the_existing_primitives(
    store: SkillStore, runner: SkillRunner
) -> None:
    wv = captured("base_hurry_turn42")
    queue = QueueStore()
    brain = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True, queue=queue)
    brain.decide(wv)
    [standing] = queue.standing(wv.faction_id)
    assert standing.faction_id == wv.faction_id
    assert standing.base_id == wv.model_dump()["base_id"]
    assert standing.until_turn == wv.turn + 5


def test_an_order_effect_is_built_by_the_channels_own_arity_check(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save(
        "skip_first_unit",
        "base production skip the first unit",
        'def on_decision(view):\n    order("skip", view["base_id"])\n    return None\n',
        surfaces=["base.production"],
    )
    wv = captured("base_production_turn42")
    brain = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True)
    brain.decide(wv)
    assert brain.last_effects == [f"skip {wv.model_dump()['base_id']}"]


# --- store, retrieve by description, re-run, versioned -------------------------------------


def test_store_retrieves_by_description_and_reruns_a_new_version(
    tmp_path: Path, runner: SkillRunner
) -> None:
    store = SkillStore(tmp_path)
    v1 = store.save("expand_when_paid", "colony pod while surplus", EXPAND)
    v2 = store.save(
        "expand_when_paid",
        "colony pod while surplus",
        EXPAND.replace("min_surplus = 2", "min_surplus = 9"),
    )
    assert (v1.version, v2.version) == (1, 2)
    assert v2.supersedes == v1.hash
    assert store.save("expand_when_paid", "colony pod while surplus", v2.code) == v2

    reopened = SkillStore(tmp_path)
    assert [s.hash for s in reopened.history("expand_when_paid")] == [v1.hash, v2.hash]
    [hit] = reopened.retrieve("build a colony pod")
    assert hit == v2
    assert reopened.retrieve("research a technology") == []

    view = captured("base_production_turn42").model_dump(mode="json", exclude_none=True)
    old = runner.run(v1.name, v1.code, "on_decision", view)
    new = runner.run(hit.name, hit.code, "on_decision", view)
    assert old.ok and old.answer["action_id"] == "unit:0"
    assert new.ok and new.answer is None  # surplus 7 < 9: the revision falls through


# --- the literal-id lint -------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        'return choose("unit:0")',
        'return choose("se:2:3")',
        'if view["base_id"] == 7:\n        return None',
        'if 3 == view["faction_id"]:\n        return None',
        'mine = [b for b in view["bases"] if b["id"] in (4, 5)]',
        'queue_answer("base.hurry", "hurry:none", [], base_id = 12)',
        'order("move", view["base_id"], 13, 8)',
        "faction_id = 2",
    ],
)
def test_the_lint_refuses_a_literal_id_skill(body: str) -> None:
    code = f"def on_decision(view):\n    {body}\n    return None\n"
    assert lint(code), code
    with pytest.raises(LintError):
        SkillStore().save("literal", "anything", code)


def test_the_lint_leaves_thresholds_and_view_reads_alone() -> None:
    assert lint(EXPAND) == []
    assert lint(HURRY) == []
    assert lint(SE) == []


# --- hermetic: file, network, time and randomness fail closed ------------------------------


@pytest.mark.parametrize(
    "body",
    [
        'return open("/etc/passwd")',
        "return time.now()",
        "return random.randint(0, 9)",
        'return http.get("http://example.invalid")',
        'return __import__("os")',
    ],
)
def test_a_skill_reaching_for_file_network_or_time_fails_closed(
    runner: SkillRunner, body: str
) -> None:
    store = SkillStore()
    store.save("escape", "base production escape", f"def on_decision(view):\n    {body}\n")
    fallback = ScriptedBrain()
    brain = SkillBrain(store, fallback, runner=runner, practice=True)
    wv = captured("base_production_turn42")
    with pytest.raises(SkillError, match="failed closed"):
        brain.decide(wv)
    result = Orchestrator(brain).decide(wv)
    assert result.orders.degraded
    assert fallback.calls == []


def test_load_is_refused_at_save_time() -> None:
    with pytest.raises(LintError):
        SkillStore().save(
            "loader", "x", 'load("os.star", "f")\ndef on_decision(view):\n    return None\n'
        )


# --- budgets fail closed --------------------------------------------------------------------


def test_a_step_budget_overrun_fails_closed_deterministically(runner: SkillRunner) -> None:
    code = (
        "def on_decision(view):\n"
        "    s = 0\n"
        "    for i in range(100000000):\n"
        "        s += i\n"
        '    return choose(view["action_space"][0]["id"])\n'
    )
    view = captured("base_production_turn42").model_dump(mode="json", exclude_none=True)
    first = runner.run("spin", code, "on_decision", view)
    second = runner.run("spin", code, "on_decision", view)
    assert (first.ok, first.failure) == (False, "steps")
    assert first == second

    store = SkillStore()
    store.save("spin", "base production spin", code)
    with pytest.raises(SkillError, match=r"\(steps\)"):
        SkillBrain(store, ScriptedBrain(), runner=runner, practice=True).decide(
            captured("base_production_turn42")
        )


def test_a_memory_budget_overrun_fails_closed() -> None:
    runner = SkillRunner(memory_bytes=64 * 1024 * 1024)
    try:
        result = runner.run(
            "hog", 'def on_decision(view):\n    return len("a" * 2000000000)\n', "on_decision", {}
        )
        assert (result.ok, result.failure) == (False, "memory")
        # The worker was replaced, not left in doubt.
        after = runner.run("ok", "def on_decision(view):\n    return None\n", "on_decision", {})
        assert after.ok and after.answer is None
    finally:
        runner.close()


def test_a_bad_answer_fails_closed(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("bad", "base production bad", "def on_decision(view):\n    return 42\n")
    with pytest.raises(SkillError, match="must return choose"):
        SkillBrain(store, ScriptedBrain(), runner=runner, practice=True).decide(
            captured("base_production_turn42")
        )


# --- no match: fallback behaviour unchanged ------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        "base_production_turn42",
        "base_production_turn135",
        "base_hurry_turn42",
        "faction_se_turn135",
    ],
)
def test_no_skill_match_gives_fallback_behaviour_unchanged(
    runner: SkillRunner, fixture: str
) -> None:
    store = SkillStore()
    store.save("tech_only", "research technology", SE, surfaces=["faction.tech"])
    wv = captured(fixture)

    direct = Orchestrator(ScriptedBrain()).decide(wv)
    fallback = ScriptedBrain()
    via = Orchestrator(SkillBrain(store, fallback, runner=runner, practice=True)).decide(wv)

    assert len(fallback.calls) == 1
    assert fallback.calls[0].model_dump() == wv.model_dump()
    assert via.orders.model_dump() == direct.orders.model_dump()


def test_a_skill_falling_through_hands_off_to_the_fallback(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("se_wealth", "faction social engineering wealth", SE, surfaces=["faction.se"])
    wv = captured("faction_se_turn42")  # offers only "No change": the skill returns None
    fallback = ScriptedBrain()
    brain = SkillBrain(store, fallback, runner=runner, practice=True)
    orders = brain.decide(wv)
    assert brain.last_skill is None
    assert len(fallback.calls) == 1
    assert orders == ScriptedBrain().decide(wv)


# --- status: candidate, verified, retired ---------------------------------------------------


def _verify(store: SkillStore, name: str, version: int) -> None:
    for seed in range(store.min_verified_seeds):
        store.record_trial(name, version, "base_production_turn42", seed, "pass", {"bases": 1})
    store.promote(name, version)


def test_a_new_version_is_a_candidate_and_does_not_answer_live(runner: SkillRunner) -> None:
    store = SkillStore()
    skill = store.save("expand_when_paid", "base production colony pod", EXPAND)
    assert skill.status == "candidate"
    wv = captured("base_production_turn42")

    fallback = ScriptedBrain()
    live = SkillBrain(store, fallback, runner=runner)
    assert live.decide(wv) == ScriptedBrain().decide(wv)
    assert live.last_skill is None and len(fallback.calls) == 1

    practice = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True)
    assert practice.decide(wv).choices[0].action_id == "unit:0"


def test_a_verified_skill_answers_live(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("expand_when_paid", "base production colony pod", EXPAND)
    _verify(store, "expand_when_paid", 1)
    wv = captured("base_production_turn42")
    fallback = ScriptedBrain()
    brain = SkillBrain(store, fallback, runner=runner)
    result = Orchestrator(brain).decide(wv)
    assert fallback.calls == []
    assert brain.last_skill is not None and brain.last_skill.status == "verified"
    assert result.orders.choices[0].action_id == "unit:0"


def test_a_newer_candidate_does_not_displace_the_verified_version_live(
    runner: SkillRunner,
) -> None:
    store = SkillStore()
    store.save("expand_when_paid", "base production colony pod", EXPAND)
    _verify(store, "expand_when_paid", 1)
    store.save(
        "expand_when_paid",
        "base production colony pod",
        EXPAND.replace("min_surplus = 2", "min_surplus = 9"),
    )
    wv = captured("base_production_turn42")
    live = SkillBrain(store, ScriptedBrain(), runner=runner)
    live.decide(wv)
    assert live.last_skill is not None and live.last_skill.version == 1
    practice = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True)
    practice.decide(wv)
    assert practice.last_skill is None  # v2 is newest, and it falls through at surplus 7


def test_a_retired_skill_is_never_retrieved(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("expand_when_paid", "base production colony pod", EXPAND)
    _verify(store, "expand_when_paid", 1)
    store.retire("expand_when_paid", 1)
    assert store.retrieve("base production colony pod") == []
    assert store.eligible(("verified", "candidate", "retired")) == []
    wv = captured("base_production_turn42")
    for practice in (False, True):
        fallback = ScriptedBrain()
        brain = SkillBrain(store, fallback, runner=runner, practice=practice)
        brain.decide(wv)
        assert brain.last_skill is None and len(fallback.calls) == 1
    with pytest.raises(ValueError, match="retired"):
        store.promote("expand_when_paid", 1)


# --- trials and promotion -------------------------------------------------------------------


def test_promotion_needs_passing_trials_from_n_distinct_seeds(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    assert store.min_verified_seeds == 3
    store.save("expand_when_paid", "colony pod", EXPAND)
    for _ in range(3):
        store.record_trial("expand_when_paid", 1, "base_production_turn42", 7, "pass")
    store.record_trial("expand_when_paid", 1, "base_production_turn135", 8, "fail", {"bases": -1})
    with pytest.raises(ValueError, match="1 distinct seed"):
        store.promote("expand_when_paid", 1)
    store.record_trial("expand_when_paid", 1, "base_production_turn135", 8, "pass", {"bases": 2})
    with pytest.raises(ValueError, match="2 distinct seed"):
        store.promote("expand_when_paid", 1)
    store.record_trial("expand_when_paid", 1, "game-0003", 9, "pass", {"bases": 1.5})
    promoted = store.promote("expand_when_paid", 1)
    assert promoted.status == "verified"
    assert promoted.hash == store.history("expand_when_paid")[0].hash  # status is not content

    reopened = SkillStore(tmp_path)
    skill = reopened.get("expand_when_paid")
    assert skill is not None and skill.status == "verified"
    assert len(skill.trials) == 6
    last = skill.trials[-1]
    assert (last.source, last.seed, last.verdict, last.deltas) == (
        "game-0003",
        9,
        "pass",
        {"bases": 1.5},
    )
    assert skill.trials[3].verdict == "fail" and skill.trials[3].deltas == {"bases": -1.0}


def test_the_seed_threshold_is_configurable() -> None:
    store = SkillStore(min_verified_seeds=1)
    store.save("expand_when_paid", "colony pod", EXPAND)
    with pytest.raises(ValueError):
        store.promote("expand_when_paid", 1)
    store.record_trial("expand_when_paid", 1, "base_production_turn42", 0, "pass")
    assert store.promote("expand_when_paid", 1).status == "verified"
    with pytest.raises(ValueError):
        SkillStore(min_verified_seeds=0)


def test_a_new_version_starts_without_trials() -> None:
    store = SkillStore()
    store.save("expand_when_paid", "colony pod", EXPAND)
    _verify(store, "expand_when_paid", 1)
    v2 = store.save("expand_when_paid", "colony pod", EXPAND.replace("= 2", "= 3"))
    assert (v2.status, v2.trials) == ("candidate", ())


# --- on_turn: policy, queue, orders ---------------------------------------------------------

#: Grow while the base is small: set growth focus, queue a hurry refusal, skip the base.
GROW = """
def on_decision(view):
    return None

def on_turn(view, focus = 1):
    set_policy("focus.growth", focus, "grow while small")
    queue_answer(
        "base.hurry", "hurry:none",
        [{"metric": "energy_reserves", "comparator": "at_most", "target": 10}],
        until_turn = view["turn"] + 3,
    )
    order("skip", view["base_id"])
"""


def test_on_turn_sets_a_knob_queues_and_orders(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("grow_small", "grow while small", GROW, requires=["focus.growth"])
    _verify(store, "grow_small", 1)
    wv = captured("base_production_turn42")
    policy, queue = PolicyStore(), QueueStore()
    brain = SkillBrain(store, ScriptedBrain(), runner=runner, policy=policy, queue=queue)
    assert brain.on_turn(wv) is None
    assert policy.knobs(wv.faction_id) == {"focus.growth": 1}
    [standing] = queue.standing(wv.faction_id)
    assert standing.until_turn == wv.turn + 3
    assert f"skip {wv.model_dump()['base_id']}" in brain.last_effects
    assert [s.name for s in brain.last_turn_skills] == ["grow_small"]


@pytest.mark.parametrize(
    "call, requires, engine",
    [
        ('set_policy("focus.growth", 5)', ["focus.growth"], None),  # out of bounds
        ('set_policy("focus.growth", 1)', [], None),  # not declared in requires
        ('set_policy("focus.greed", 1)', ["focus.greed"], ["focus.greed"]),  # not in registry
        ("set_policy(3, 1)", [], None),  # not a knob id
    ],
)
def test_a_bad_knob_fails_closed_and_applies_nothing(
    runner: SkillRunner, call: str, requires: list[str], engine: list[str] | None
) -> None:
    code = (
        "def on_decision(view):\n    return None\n\n"
        "def on_turn(view):\n"
        '    order("skip", view["base_id"])\n'
        '    set_policy("focus.tech", 1, "fine on its own")\n'
        f"    {call}\n"
    )
    store = SkillStore()
    store.save("bad_knob", "bad knob", code, requires=[*requires, "focus.tech"])
    knobs = None if engine is None else [*engine, "focus.tech"]
    policy, orders = PolicyStore(), []
    brain = SkillBrain(
        store,
        ScriptedBrain(),
        runner=runner,
        practice=True,
        policy=policy,
        engine_knobs=knobs,
    )
    brain.orders = type("Channel", (), {"issue": lambda self, c: orders.append(c)})()  # type: ignore[assignment]
    wv = captured("base_production_turn42")
    with pytest.raises(SkillError, match="invalid effect"):
        brain.on_turn(wv)
    assert policy.knobs(wv.faction_id) == {}
    assert orders == []


def test_on_turn_must_return_nothing(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save(
        "talky",
        "talky",
        "def on_decision(view):\n    return None\n\ndef on_turn(view):\n    return 1\n",
    )
    brain = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True)
    with pytest.raises(SkillError, match="returns nothing"):
        brain.on_turn(captured("base_production_turn42"))


def test_on_turn_runs_under_the_same_step_budget() -> None:
    small = SkillRunner(max_ticks=5)
    try:
        store = SkillStore()
        store.save(
            "spin_turn",
            "spin",
            "def on_decision(view):\n    return None\n\n"
            "def on_turn(view):\n    for i in range(100000000):\n        pass\n",
        )
        brain = SkillBrain(store, ScriptedBrain(), runner=small, practice=True)
        with pytest.raises(SkillError, match=r"\(steps\)"):
            brain.on_turn(captured("base_production_turn42"))
    finally:
        small.close()


def test_set_policy_from_on_decision_fails_closed(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save(
        "sneaky",
        "base production sneaky",
        'def on_decision(view):\n    set_policy("focus.fight", 1)\n    return None\n',
        requires=["focus.fight"],
    )
    brain = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True)
    with pytest.raises(SkillError, match="set_policy is for on_turn"):
        brain.decide(captured("base_production_turn42"))


def test_on_turn_skips_skills_without_one_and_honours_names(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("expand_when_paid", "colony pod", EXPAND)
    store.save("grow_small", "grow while small", GROW, requires=["focus.growth"])
    policy = PolicyStore()
    brain = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True, policy=policy)
    wv = captured("base_production_turn42")
    brain.on_turn(wv, names=["expand_when_paid"])
    assert brain.last_turn_skills == [] and policy.knobs(wv.faction_id) == {}
    brain.on_turn(wv)
    assert [s.name for s in brain.last_turn_skills] == ["grow_small"]


# --- requires: a knob this engine lacks ------------------------------------------------------


def test_retrieval_drops_a_skill_needing_a_knob_the_engine_lacks(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("expand_when_paid", "base production colony pod", EXPAND, requires=["focus.growth"])
    assert store.retrieve("colony pod", knobs=["focus.fight"]) == []
    assert len(store.retrieve("colony pod", knobs=["focus.growth"])) == 1
    wv = captured("base_production_turn42")

    lacking = SkillBrain(
        store, ScriptedBrain(), runner=runner, practice=True, engine_knobs=["focus.fight"]
    )
    lacking.decide(wv)
    assert lacking.last_skill is None

    full = SkillBrain(store, ScriptedBrain(), runner=runner, practice=True)
    assert full.decide(wv).choices[0].action_id == "unit:0"


def test_requires_must_name_knob_ids() -> None:
    with pytest.raises(LintError, match="requires"):
        SkillStore().save("x", "x", EXPAND, requires=["growth"])
