"""Queued answers: standing until the board says otherwise (na-7bk slice 2).

The mechanism is only worth having if it can STOP. An answer that repeats is a script, and the
difference between this and a script is that the agent had to say in advance, in measurable
terms, what would make it wrong. So most of this file is about the stopping.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.brain import Brain
from neural_amplifier.contract import Orders, WorldView
from neural_amplifier.queued import Predicate, QueuedAnswer, QueueError, QueueStore, validate
from neural_amplifier.service import create_app

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
    "metrics": {"mineral_surplus": 5, "drone_total": 1},
    "action_space": [
        {"id": "unit:0", "action": "Colony Pod", "cost": 30},
        {"id": "facility:4", "action": "Recycling Tanks", "cost": 40},
    ],
}


class CountingBrain(Brain):
    """Records every time it was woken. Not being woken is the point of the feature.

    Answers with a legal action rather than nothing, deliberately: empty orders are a decision
    where everything was thrown out, which sends the orchestrator round its repair loop and asks
    the brain a SECOND time for one decision. That would make every count here read double and
    the counts are the measurement.
    """

    name = "counting"

    def __init__(self) -> None:
        self.asked = 0
        self.advisories: list[list[str]] = []

    def decide(self, world_view: WorldView) -> Orders:
        from neural_amplifier.contract import Choice

        self.asked += 1
        self.advisories.append(list(world_view.advisories or []))
        return Orders(choices=[Choice(action_id="unit:0", reason="woken")])


def install(client: TestClient, **overrides) -> dict:
    body = {
        "faction_id": 1,
        "surface_id": "base.production",
        "base_id": 7,
        "action_id": "facility:4",
        "reason": "finish the Tanks",
        "predicates": [{"metric": "mineral_surplus", "comparator": "at_least", "target": 2}],
    }
    body.update(overrides)
    return client.post("/agent/queue", json=body)


# --- standing ------------------------------------------------------------------


def test_a_standing_answer_answers_without_waking_the_brain() -> None:
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    assert install(client).status_code == 200

    orders = client.post("/decide", json=BASE_VIEW).json()

    assert [c["action_id"] for c in orders["choices"]] == ["facility:4"]
    assert not orders.get("degraded")
    assert brain.asked == 0, "the whole point is that the agent is not woken"


def test_it_is_counted_so_the_mechanism_can_be_judged() -> None:
    client = TestClient(create_app(brain=CountingBrain()))
    install(client)
    for _ in range(3):
        client.post("/decide", json=BASE_VIEW)

    standing = client.get("/agent/queue").json()["standing"]
    assert standing[0]["applied"] == 3


# --- stopping ------------------------------------------------------------------


def test_a_violated_predicate_wakes_the_agent_and_names_what_changed() -> None:
    """Being woken with no explanation is how an agent re-queues the same answer."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)

    client.post("/decide", json=BASE_VIEW)
    assert brain.asked == 0

    collapsed = {**BASE_VIEW, "metrics": {"mineral_surplus": 0, "drone_total": 1}}
    client.post("/decide", json=collapsed)

    assert brain.asked == 1, "a violated predicate must re-raise the decision"
    advisory = " ".join(brain.advisories[-1])
    assert "no longer holds" in advisory
    assert "mineral_surplus is 0" in advisory, "the agent must be told what the board says"


def test_an_overtaken_answer_is_retired_rather_than_left_to_fire_again() -> None:
    client = TestClient(create_app(brain=CountingBrain()))
    install(client)
    client.post("/decide", json={**BASE_VIEW, "metrics": {"mineral_surplus": 0}})

    queue = client.get("/agent/queue").json()
    assert queue["count"] == 0, "an overtaken answer must not stand again"
    assert queue["retired"], "and it must leave a record of what overtook it"
    assert "mineral_surplus is 0" in " ".join(queue["retired"][0]["retired_because"])


def test_a_missing_metric_stops_the_answer_rather_than_passing_it() -> None:
    """Fail-closed. An answer applying on the strength of a metric the adapter did not send this
    turn is an answer standing on an unread instrument — absence of evidence is not evidence the
    answer is still right."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)

    client.post("/decide", json={**BASE_VIEW, "metrics": {"drone_total": 1}})
    assert brain.asked == 1
    assert "not reported" in " ".join(brain.advisories[-1])


def test_the_horizon_stops_it_even_while_every_predicate_holds() -> None:
    """A standing answer with no expiry is an order nobody revisits, which is the failure this
    bead exists to avoid."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client, until_turn=42)

    client.post("/decide", json=BASE_VIEW)
    assert brain.asked == 0

    client.post("/decide", json={**BASE_VIEW, "turn": 43})
    assert brain.asked == 1
    assert "horizon passed" in " ".join(brain.advisories[-1])


def test_an_action_the_engine_stopped_offering_stops_the_answer() -> None:
    """The invariant-1 backstop, for a decision the brain is not being asked about.

    An action space is rebuilt every time the engine asks; an item already built, or whose
    prerequisite was lost, is simply gone. Answering with it would be an id the engine never
    offered — the one failure a standing answer must not be able to cause silently.
    """
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client)

    built = {**BASE_VIEW, "action_space": [{"id": "unit:0", "action": "Colony Pod", "cost": 30}]}
    client.post("/decide", json=built)

    assert brain.asked == 1
    assert "no longer in the action space" in " ".join(brain.advisories[-1])


def test_every_violated_predicate_is_reported_not_just_the_first() -> None:
    """One reason at a time invites the agent to answer that one and re-queue into the next
    wake-up."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(
        client,
        predicates=[
            {"metric": "mineral_surplus", "comparator": "at_least", "target": 2},
            {"metric": "drone_total", "comparator": "at_most", "target": 2},
        ],
    )

    client.post("/decide", json={**BASE_VIEW, "metrics": {"mineral_surplus": 0, "drone_total": 9}})
    advisory = " ".join(brain.advisories[-1])
    assert "mineral_surplus is 0" in advisory
    assert "drone_total is 9" in advisory


# --- refusals, while the agent can still be told ---------------------------------


def test_a_predicate_that_can_never_fire_is_refused_at_queue_time() -> None:
    """The refusal IS the feature.

    `directives.py` refuses an unmeasurable directive because it fails silently for the rest of
    the game. A queued answer is worse: the directive only failed to steer, this one keeps
    answering, so an uncheckable predicate degrades it into the script it was meant not to be.
    """
    client = TestClient(create_app(brain=CountingBrain()))
    resp = install(client, predicates=[{"metric": "at_war", "target": 1}])
    assert resp.status_code == 422
    assert "unknown metric" in resp.json()["detail"]
    # And it names the vocabulary, so the agent can fix it rather than guess again.
    assert "mineral_surplus" in resp.json()["detail"]


def test_an_answer_with_no_predicates_is_refused() -> None:
    client = TestClient(create_app(brain=CountingBrain()))
    resp = install(client, predicates=[])
    assert resp.status_code == 422
    assert "script" in resp.json()["detail"]


def test_validate_refuses_an_unknown_comparator() -> None:
    with pytest.raises(QueueError, match="unknown comparator"):
        validate([Predicate(metric="mineral_surplus", comparator="roughly", target=2)])  # type: ignore[arg-type]


# --- the fog boundary applies to standing answers too -----------------------------


def test_one_factions_standing_answer_never_answers_anothers_decision() -> None:
    """Base ids are one engine-wide sequence, so a base id alone is a coincidence away from
    applying faction 2's standing answer to faction 4's decision."""
    brain = CountingBrain()
    client = TestClient(create_app(brain=brain))
    install(client, faction_id=1)

    # NEGATIVE: same surface, same base number, different faction.
    other = {**BASE_VIEW, "faction_id": 4, "faction": "SPARTANS"}
    client.post("/decide", json=other)
    assert brain.asked == 1, "faction 4 must be asked, not answered from faction 1's queue"

    # POSITIVE control: the same queue DOES answer its own faction, so the negative above is the
    # boundary holding rather than the queue being broken.
    client.post("/decide", json=BASE_VIEW)
    assert brain.asked == 1, "faction 1's own decision must still be answered from the queue"


def test_the_queue_listing_can_be_scoped_to_one_faction() -> None:
    client = TestClient(create_app(brain=CountingBrain()))
    install(client, faction_id=1, base_id=7)
    install(client, faction_id=4, base_id=9)

    assert client.get("/agent/queue", params={"faction_id": 1}).json()["count"] == 1
    assert client.get("/agent/queue").json()["count"] == 2


# --- store-level -----------------------------------------------------------------


def test_re_queueing_the_same_decision_replaces_the_answer() -> None:
    store = QueueStore()
    p = [Predicate(metric="mineral_surplus", comparator="at_least", target=2)]
    store.install(
        QueuedAnswer(faction_id=1, surface_id="s", action_id="a", predicates=p, base_id=1)
    )
    store.install(
        QueuedAnswer(faction_id=1, surface_id="s", action_id="b", predicates=p, base_id=1)
    )
    standing = store.standing()
    assert len(standing) == 1
    assert standing[0].action_id == "b"


def test_a_faction_scope_answer_and_a_base_answer_are_different_entries() -> None:
    """`faction.se` has no base; `base.production` does. They must not collide."""
    store = QueueStore()
    p = [Predicate(metric="energy_reserves", comparator="at_least", target=0)]
    store.install(
        QueuedAnswer(faction_id=1, surface_id="faction.se", action_id="se:none", predicates=p)
    )
    store.install(
        QueuedAnswer(
            faction_id=1, surface_id="base.production", action_id="unit:0", predicates=p, base_id=7
        )
    )
    assert len(store.standing()) == 2


def test_the_queue_endpoints_work_when_the_brain_is_an_attached_agent() -> None:
    """The regression test for a shadowing bug mypy found and no test could see.

    `create_app` binds the AgentBrain's DecisionQueue to a local called `queue`, in the same
    function scope where the standing-answer store was first also called `queue`. The endpoints
    close over the name, so with an attached agent they would have operated on the decision queue
    instead — installing an answer onto the wrong object entirely.

    Every other test in this file uses a non-agent brain, so the rebinding never happened and all
    fifteen passed while the production path was broken. Hence this one: the same install and
    list, with the brain that triggers the rebinding.
    """
    from neural_amplifier.agent_brain import AgentBrain
    from neural_amplifier.doorbell import Doorbell

    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=10)
    client = TestClient(create_app(brain=brain))

    assert install(client).status_code == 200
    listing = client.get("/agent/queue").json()
    assert listing["count"] == 1
    assert listing["standing"][0]["action_id"] == "facility:4"

    # And the decision queue is still itself: the agent-facing endpoints must be unaffected.
    assert client.post("/agent/next", json={"wait": 0}).json()["decision_id"] is None
