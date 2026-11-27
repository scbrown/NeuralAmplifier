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
    brain = SkillBrain(store, fallback, runner=runner, queue=QueueStore())
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
    brain = SkillBrain(store, ScriptedBrain(), runner=runner, queue=queue)
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
    brain = SkillBrain(store, ScriptedBrain(), runner=runner)
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
    brain = SkillBrain(store, fallback, runner=runner)
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
        SkillBrain(store, ScriptedBrain(), runner=runner).decide(captured("base_production_turn42"))


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
        SkillBrain(store, ScriptedBrain(), runner=runner).decide(captured("base_production_turn42"))


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
    via = Orchestrator(SkillBrain(store, fallback, runner=runner)).decide(wv)

    assert len(fallback.calls) == 1
    assert fallback.calls[0].model_dump() == wv.model_dump()
    assert via.orders.model_dump() == direct.orders.model_dump()


def test_a_skill_falling_through_hands_off_to_the_fallback(runner: SkillRunner) -> None:
    store = SkillStore()
    store.save("se_wealth", "faction social engineering wealth", SE, surfaces=["faction.se"])
    wv = captured("faction_se_turn42")  # offers only "No change": the skill returns None
    fallback = ScriptedBrain()
    brain = SkillBrain(store, fallback, runner=runner)
    orders = brain.decide(wv)
    assert brain.last_skill is None
    assert len(fallback.calls) == 1
    assert orders == ScriptedBrain().decide(wv)
