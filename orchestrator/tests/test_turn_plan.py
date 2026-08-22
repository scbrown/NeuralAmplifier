"""Bulk-turn mode: strategy set once per turn, answered in milliseconds (na-7bk).

The mechanism exists so an agent is not woken per decision, so most assertions here count
wake-ups. The rest are about the table's two hard edges: it answers nothing outside its stated
turn, and it never answers with an action the engine has stopped offering.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.brain import Brain
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.policy import SurfacePolicy
from neural_amplifier.service import create_app
from neural_amplifier.turnplan import PlanEntry, PlanError, PlanStore, TurnPlan, validate

BASE_VIEW = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "base",
    "surface_id": "base.production",
    "turn": 42,
    "faction": "Gaians",
    "faction_id": 1,
    "base": "Gaia's Landing",
    "base_id": 7,
    "metrics": {"mineral_surplus": 5},
    "action_space": [
        {"id": "unit:0", "action": "Colony Pod", "cost": 30},
        {"id": "facility:4", "action": "Recycling Tanks", "cost": 40},
    ],
}

FACTION_VIEW = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "turn",
    "surface_id": "faction.se",
    "turn": 42,
    "faction": "Gaians",
    "faction_id": 1,
    "metrics": {"energy_reserves": 80},
    "action_space": [
        {"id": "se:no-change", "action": "No change"},
        {"id": "se:democratic", "action": "Democratic"},
    ],
}


class CountingBrain(Brain):
    """Records every time it was woken. Not being woken is the point of the feature."""

    name = "counting"

    def __init__(self) -> None:
        self.asked = 0
        self.advisories: list[list[str]] = []

    def decide(self, world_view: WorldView) -> Orders:
        self.asked += 1
        self.advisories.append(list(world_view.advisories or []))
        return Orders(choices=[Choice(action_id="unit:0", reason="woken")])


def install(client: TestClient, **overrides) -> dict:
    body = {
        "faction_id": 1,
        "turn": 42,
        "entries": [
            {
                "surface_id": "base.production",
                "base_id": 7,
                "action_id": "facility:4",
                "reason": "finish the Tanks",
            }
        ],
    }
    body.update(overrides)
    return client.post("/agent/plan", json=body)


# --- answering from the table ------------------------------------------------------


def test_a_covered_decision_is_answered_without_waking_the_brain() -> None:
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    assert install(client).status_code == 200

    orders = client.post("/decide", json=BASE_VIEW).json()

    assert [c["action_id"] for c in orders["choices"]] == ["facility:4"]
    assert not orders.get("degraded")
    assert brain.asked == 0, "the whole point is that the agent is not woken"


def test_the_record_says_plan_not_llm(tmp_path: Path) -> None:
    """The bead's own requirement: replay must tell strategy-driven answers from agent-driven
    ones, and the tier is the only place that distinction survives."""
    log_path = tmp_path / "decisions.jsonl"
    client = TestClient(create_app(brain=CountingBrain(), log=DecisionLog(log_path)))
    install(client)
    client.post("/decide", json=BASE_VIEW)

    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(lines) == 1, "exactly one decision record per decision, on every path"
    assert lines[0]["tier"] == "plan"
    assert not lines[0]["degraded"]


def test_a_re_asked_decision_is_answered_again_and_counted() -> None:
    """The engine re-asks bases within a turn (21/24 base-turns fired twice, measured), and the
    second ask is the same planned decision on the same board."""
    client = TestClient(create_app(brain=CountingBrain()))
    install(client)
    client.post("/decide", json=BASE_VIEW)
    client.post("/decide", json=BASE_VIEW)

    plans = client.get("/agent/plan").json()["plans"]
    assert plans[0]["entries"][0]["applied"] == 2


def test_a_faction_scope_entry_answers_a_baseless_decision() -> None:
    """base_id None is the faction-scope key (faction.se and the like), not a wildcard."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(
        client,
        entries=[{"surface_id": "faction.se", "action_id": "se:no-change"}],
    )

    orders = client.post("/decide", json=FACTION_VIEW).json()
    assert [c["action_id"] for c in orders["choices"]] == ["se:no-change"]
    assert brain.asked == 0


# --- what the table does NOT answer -------------------------------------------------


def test_an_uncovered_decision_wakes_the_brain_as_before() -> None:
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)

    client.post("/decide", json={**BASE_VIEW, "base_id": 9, "base": "Vale of Winds"})
    assert brain.asked == 1, "the table covers what it names, nothing more"


def test_NEGATIVE_another_factions_plan_never_answers_this_decision() -> None:
    """Bases are numbered in one engine-wide sequence, so a base id alone is a coincidence away
    from applying faction 1's strategy to faction 4's decision. The faction is part of the key."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)  # faction 1's table

    client.post("/decide", json={**BASE_VIEW, "faction_id": 4, "faction": "Spartans"})
    assert brain.asked == 1


def test_POSITIVE_the_owning_faction_is_answered() -> None:
    """The pair for the probe above: absence alone proves nothing — a table that is broken or
    empty passes the negative probe perfectly."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)

    orders = client.post("/decide", json=BASE_VIEW).json()
    assert [c["action_id"] for c in orders["choices"]] == ["facility:4"]
    assert brain.asked == 0


def test_a_table_answers_nothing_outside_its_stated_turn() -> None:
    """By turn N+1 the engine has played turn N, so a turn-N answer was made against a board
    that no longer exists. Silence re-raises the decision to the agent, which is honest."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client, turn=42)

    client.post("/decide", json={**BASE_VIEW, "turn": 43})
    assert brain.asked == 1


def test_a_planned_action_the_engine_stopped_offering_wakes_the_brain_with_the_reason() -> None:
    """Invariant 1's backstop. The plan came from the forecast; the space in front of us is the
    engine's current word, and an item already built is simply gone."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)

    built_already = {
        **BASE_VIEW,
        "action_space": [{"id": "unit:0", "action": "Colony Pod", "cost": 30}],
    }
    client.post("/decide", json=built_already)

    assert brain.asked == 1
    advisory = " ".join(brain.advisories[-1])
    assert "facility:4" in advisory and "no longer in the action space" in advisory

    missed = client.get("/agent/plan").json()["missed"]
    assert missed and missed[0]["action_id"] == "facility:4"

    # Retired, not left to fail again: the same decision re-raised carries no repeat advisory,
    # because an entry naming a gone action can never apply for the rest of the turn.
    client.post("/decide", json=built_already)
    assert brain.asked == 2
    assert brain.advisories[-1] == []


def test_installing_replaces_the_previous_table_whole() -> None:
    """Merging would leave stale answers from an earlier reading of the same turn standing
    behind the new ones — the exact confusion a per-turn table exists to rule out."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)
    install(
        client,
        entries=[
            {
                "surface_id": "base.production",
                "base_id": 9,
                "action_id": "unit:0",
            }
        ],
    )

    client.post("/decide", json=BASE_VIEW)  # base 7: covered by the FIRST table only
    assert brain.asked == 1, "the first table's entry must not survive the second install"


def test_an_empty_install_means_wake_me_for_everything() -> None:
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)
    install(client, entries=[])

    client.post("/decide", json=BASE_VIEW)
    assert brain.asked == 1


def test_a_switched_off_surface_stays_deterministic_even_when_planned(tmp_path: Path) -> None:
    """Off is DETERMINISTIC, never a side door. The policy gate runs before the table, so a
    surface the configuration hands to the engine cannot be answered by an agent's plan either."""
    log_path = tmp_path / "decisions.jsonl"
    store = PlanStore()
    store.install(
        TurnPlan(
            faction_id=1,
            turn=42,
            entries={
                ("base.production", 7): PlanEntry(
                    surface_id="base.production", base_id=7, action_id="facility:4"
                )
            },
        )
    )
    orchestrator = Orchestrator(
        brain=CountingBrain(),
        log=DecisionLog(log_path),
        # `source` set: a policy with no source is "nobody has an opinion" and allows
        # everything, which would make this test pass vacuously with the gate not running.
        policy=SurfacePolicy(toggles={"base.production": False}, source=Path("na.toml")),
        turn_plan=store,
    )
    result = orchestrator.decide(WorldView.model_validate(BASE_VIEW))

    assert result.record.tier == "deterministic"
    assert not result.orders.choices


# --- refusals ------------------------------------------------------------------------


def test_a_plan_with_no_turn_is_refused() -> None:
    client = TestClient(create_app(brain=CountingBrain()))
    resp = install(client, turn=None)
    assert resp.status_code == 422
    assert "turn" in resp.json()["detail"]


def test_an_entry_with_no_action_is_refused() -> None:
    """A plan entry with no answer is a note; the queue of uncovered decisions handles those."""
    client = TestClient(create_app(brain=CountingBrain()))
    resp = install(client, entries=[{"surface_id": "base.production", "base_id": 7}])
    assert resp.status_code == 422
    assert "action_id" in resp.json()["detail"]


def test_validate_refuses_an_entry_that_matches_nothing() -> None:
    with pytest.raises(PlanError, match="surface_id"):
        validate(
            TurnPlan(
                faction_id=1,
                turn=42,
                entries={("", None): PlanEntry(surface_id="", action_id="unit:0")},
            )
        )
