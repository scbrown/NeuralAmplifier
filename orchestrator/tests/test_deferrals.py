"""Deferred decisions: the agent answers the engine now and itself later (na-7bk).

The thing under test is a distinction, not a feature. A deferral and a degradation both hand the
engine its own pick, and every assertion here is about telling them apart — in the record, in the
metrics, and in what the agent is left holding afterwards.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.brain import Brain
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.deferrals import Deferral, DeferralSet, is_defer
from neural_amplifier.service import create_app

WORLD_VIEW = {
    "schema_version": "0.1",
    "engine": "thinker",
    "scope": "base",
    "surface_id": "base.production",
    "turn": 42,
    "faction": "Gaians",
    "faction_id": 1,
    "base": "Gaia's Landing",
    "base_id": 7,
    "metrics": {"energy_reserves": 82},
    "native_choice": "unit:1",
    "action_space": [
        {"id": "unit:0", "action": "Colony Pod", "cost": 30},
        {"id": "unit:1", "action": "Formers", "cost": 14},
        {"id": "facility:4", "action": "Recycling Tanks", "cost": 40},
    ],
}


class DeferringBrain(Brain):
    """Answers `defer` to everything, which is the whole point of it."""

    name = "deferring"

    def __init__(self, action_id: str = "defer") -> None:
        self.action_id = action_id
        self.asked = 0

    def decide(self, world_view: WorldView) -> Orders:
        self.asked += 1
        return Orders(choices=[Choice(action_id=self.action_id, reason="thinking about it")])


def fake_adapter(game_dir: Path, ok: bool = True, detail: str = "done") -> threading.Thread:
    """Stand in for na_command_tick: consume the command, then write a result."""

    def run() -> None:
        cmd = game_dir / "na-command"
        for _ in range(400):
            if cmd.exists():
                line = cmd.read_text(encoding="utf-8").strip()
                cmd.unlink()
                (game_dir / "na-command-result").write_text(
                    json.dumps(
                        {
                            "command": line.split()[0] if line else "",
                            "detail": detail,
                            "ok": ok,
                            "turn": 42,
                            "halted": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


# --- the distinction ----------------------------------------------------------


def test_a_deferral_is_not_a_degradation() -> None:
    """The single most important assertion in this file.

    Both apply the engine's own pick. If `deferred` were recorded as `deterministic` or as
    degraded, a run in which the agent was thinking carefully would be indistinguishable from a
    run in which no brain was attached — and `degrade_rate` is the number that exists to catch
    exactly the second case.
    """
    client = TestClient(create_app(brain=DeferringBrain()))
    orders = client.post("/decide", json=WORLD_VIEW).json()

    # The engine is answered immediately, and told `defer` rather than told nothing. Either way
    # it applies its own pick — an adapter that has never heard of deferral reads `defer` as an
    # unparseable action id, which already falls back to native_choice — but relaying it is what
    # lets the ADAPTER's own record say `deferred` too. Empty orders would reach it as "no
    # action_id in reply", losing the distinction in the game's primary telemetry.
    assert [c["action_id"] for c in orders["choices"]] == ["defer"]
    assert orders["choices"][0]["reason"] == "thinking about it"
    assert not orders.get("degraded")

    pending = client.get("/agent/pending").json()
    assert pending["count"] == 1
    parked = pending["pending"][0]
    assert parked["status"] == "open"
    assert parked["base_id"] == 7
    assert parked["faction_id"] == 1
    # What stood in the meantime, by name — so an expiry can say what actually got built.
    assert parked["standing_action_id"] == "unit:1"
    assert parked["standing_action"] == "Formers"


def test_defer_is_not_reported_as_an_action_the_engine_did_not_offer() -> None:
    """`defer` is in no action space, so it must be recognised BEFORE validation.

    Caught a moment later, it would be a perfectly correct complaint about an unknown id — the
    agent told its answer "was not applied", which is true and useless. It did not fail to
    answer; it declined to answer yet.
    """
    brain = DeferringBrain()
    client = TestClient(create_app(brain=brain))
    client.post("/decide", json=WORLD_VIEW)
    # One ask. A defer is not a repairable mistake, so it must not be re-asked.
    assert brain.asked == 1


@pytest.mark.parametrize("spelling", ["defer", "DEFER", " Defer "])
def test_defer_is_matched_forgivingly(spelling: str) -> None:
    """Deliberately unlike the rest of the action-space contract, which is exact.

    A near-miss on a real action id is checked against a list and reported. A near-miss on
    `defer` is checked against nothing — so without this it would be validated away as an unknown
    id, and the agent would never learn it nearly worked.
    """
    assert is_defer(spelling)
    client = TestClient(create_app(brain=DeferringBrain(spelling)))
    client.post("/decide", json=WORLD_VIEW)
    assert client.get("/agent/pending").json()["count"] == 1


def test_a_mixed_answer_defers_the_whole_decision() -> None:
    """`[defer, unit:0]` says two things about one decision, and door 1 consumes one item id.

    "Build a Colony Pod AND think about it" is not expressible, so the conservative half wins:
    the engine's own pick stands and the decision stays open.
    """

    class MixedBrain(Brain):
        name = "mixed"

        def decide(self, world_view: WorldView) -> Orders:
            return Orders(choices=[Choice(action_id="defer"), Choice(action_id="unit:0")])

    client = TestClient(create_app(brain=MixedBrain()))
    orders = client.post("/decide", json=WORLD_VIEW).json()
    # The concrete half does NOT ride along: one item id reaches the engine, and it is not this.
    assert [c["action_id"] for c in orders["choices"]] == ["defer"]
    assert client.get("/agent/pending").json()["count"] == 1


# --- resolution, through the door that already exists -------------------------


def test_a_confirmed_build_resolves_the_deferral_it_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolved through door 2's existing `build` verb, not a second endpoint meaning the same.

    The link back is reconstructed from the order's own arguments, because `base_id` is the term
    both sides hold — `POST /order` carries a base id and an item id and nothing else, which is
    the engine's own grammar.
    """
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)
    assert client.get("/agent/pending").json()["count"] == 1

    fake_adapter(tmp_path, ok=True, detail="base 7 -> Recycling Tanks")
    got = client.post("/order", json={"verb": "build", "args": [7, -4]}).json()

    assert got["status"] == "ok"
    # Named in the same response that retired it: no window where /agent/pending still offers
    # work the agent has already done.
    assert got["resolved_deferrals"], "a confirmed build must close the deferral it answers"
    assert client.get("/agent/pending").json()["count"] == 0


def test_an_unconfirmed_order_leaves_the_deferral_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`unknown` is a real outcome and is never upgraded to "applied" on silence.

    Retiring the agent's outstanding work on the strength of a message that may never have been
    read is the one way this mechanism could lose a decision entirely — the deferral would be
    gone and the build would not have happened.
    """
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)

    # No adapter running: the command is written and nothing ever answers it.
    got = client.post("/order", json={"verb": "build", "args": [7, -4], "timeout_s": 0.2}).json()
    assert got["status"] != "ok"
    assert "resolved_deferrals" not in got
    assert client.get("/agent/pending").json()["count"] == 1


def test_a_build_for_another_base_resolves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)

    fake_adapter(tmp_path, ok=True, detail="base 99 -> Formers")
    got = client.post("/order", json={"verb": "build", "args": [99, -4]}).json()
    assert got["status"] == "ok"
    assert "resolved_deferrals" not in got
    assert client.get("/agent/pending").json()["count"] == 1


def batch_adapter(game_dir: Path, oks: list[bool]) -> None:
    """Stand in for na_order_batch: one envelope, one entry per order it ran, in order."""

    def run() -> None:
        cmd = game_dir / "na-command"
        for _ in range(400):
            if cmd.exists():
                lines = cmd.read_text(encoding="utf-8").strip().splitlines()
                cmd.unlink()
                results = [
                    {"command": line.split()[0], "detail": "done" if ok else "refused", "ok": ok}
                    for line, ok in zip(lines, oks, strict=False)
                ]
                (game_dir / "na-command-result").write_text(
                    json.dumps(
                        {
                            "command": "batch",
                            "results": results,
                            "count": len(results),
                            "dropped": 0,
                            "ok": all(r["ok"] for r in results),
                            "turn": 42,
                            "halted": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    threading.Thread(target=run, daemon=True).start()


def test_a_confirmed_build_in_a_batch_resolves_the_deferral_it_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweeping the pending set is the batch's main use — an agent that parked six decisions
    answers them in one round trip, and each confirmed build must retire its deferral exactly as
    a single one does. Before this path resolved, a swept batch left /agent/pending offering
    work the agent had already done."""
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)
    assert client.get("/agent/pending").json()["count"] == 1

    batch_adapter(tmp_path, oks=[True, True])
    got = client.post(
        "/order",
        json={
            "orders": [
                {"verb": "move", "args": [3, 10, 10]},
                {"verb": "build", "args": [7, -4]},
            ]
        },
    ).json()

    assert got["status"] == "ok"
    assert got["resolved_deferrals"], "a confirmed build in a batch must close its deferral"
    assert client.get("/agent/pending").json()["count"] == 0


def test_an_unconfirmed_build_in_a_batch_leaves_the_deferral_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmation is per ORDER: the move landing does not vouch for the build. Only the
    build's own result entry can retire the deferral it answers."""
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)

    batch_adapter(tmp_path, oks=[True, False])
    got = client.post(
        "/order",
        json={
            "orders": [
                {"verb": "move", "args": [3, 10, 10]},
                {"verb": "build", "args": [7, -4]},
            ]
        },
    ).json()

    assert got["status"] == "refused"
    assert "resolved_deferrals" not in got
    assert client.get("/agent/pending").json()["count"] == 1


# --- expiry is honest, not tidy ------------------------------------------------


def test_the_turn_moving_expires_an_unresolved_deferral() -> None:
    """By turn 43 the engine has played turn 42's build and the base's minerals have moved.

    An answer arriving now applies to a different board, so it is not a late answer to this
    decision. Recording it as a resolution would be a lie about which turn it was made on.
    """
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)
    assert client.get("/agent/pending").json()["count"] == 1

    client.post("/decide", json={**WORLD_VIEW, "turn": 43, "base_id": 8})
    pending = client.get("/agent/pending").json()
    # Turn 42's is expired; turn 43's own deferral is the one now open.
    assert [p["base_id"] for p in pending["pending"]] == [8]


def test_an_expired_deferral_says_what_stood_instead() -> None:
    """Not an error and not a success — the engine's choice stood, because we did not come back.

    Recorded in those words, because a closed deferral with no reason reads as "handled".
    """
    deferrals = DeferralSet()
    deferrals.open(
        Deferral(
            id="d1",
            surface_id="base.production",
            turn=42,
            base_id=7,
            standing_action_id="unit:1",
            standing_action="Formers",
        )
    )
    expired = deferrals.expire_before(43)
    assert len(expired) == 1
    assert expired[0].status == "expired"
    assert "Formers" in (expired[0].reason or "")
    assert "stood" in (expired[0].reason or "")


def test_a_resolved_deferral_is_not_expired_afterwards() -> None:
    deferrals = DeferralSet()
    deferrals.open(Deferral(id="d1", surface_id="base.production", turn=42, base_id=7))
    assert deferrals.resolve_for_base(7, resolution="build 7 -4")
    assert deferrals.expire_before(99) == []
    assert deferrals.get("d1").status == "resolved"  # type: ignore[union-attr]


def test_re_deferring_the_same_decision_replaces_it() -> None:
    """The engine asks a base several times per turn, so the same decision genuinely re-defers.

    Two entries for one base would show the agent duplicate work and invite it to answer the
    same thing twice through door 2.
    """
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)
    client.post("/decide", json=WORLD_VIEW)
    assert client.get("/agent/pending").json()["count"] == 1


def test_the_parked_world_view_is_available_for_the_answer() -> None:
    """Coming back to a decision must not mean re-deriving the situation from memory."""
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=WORLD_VIEW)

    assert "world_view" not in client.get("/agent/pending").json()["pending"][0]
    full = client.get("/agent/pending", params={"full": "true"}).json()["pending"][0]
    assert full["world_view"]["base"] == "Gaia's Landing"
    assert [a["id"] for a in full["world_view"]["action_space"]] == [
        "unit:0",
        "unit:1",
        "facility:4",
    ]


def test_eviction_drops_closed_entries_before_open_ones() -> None:
    """A closed deferral is history; an open one is outstanding work."""
    deferrals = DeferralSet(max_open=3)
    for i in range(3):
        deferrals.open(Deferral(id=f"closed{i}", surface_id="s", turn=1, base_id=i))
        deferrals.resolve(f"closed{i}", resolution="done")
    for i in range(3):
        deferrals.open(Deferral(id=f"open{i}", surface_id="s", turn=1, base_id=100 + i))

    surviving = {d.id for d in deferrals.all()}
    assert {"open0", "open1", "open2"} <= surviving
    assert len(deferrals.pending()) == 3


def test_a_withheld_native_choice_is_recorded_as_unknown_not_guessed() -> None:
    """`base.production` withholds the engine's pick from the /decide body on purpose (na-glk).

    So a deferral there genuinely does not know what stood, and must say so. The trap is
    `fallback_action_id()`, which is a different concept wearing a similar name — the
    orchestrator's own degradation target, not the engine's choice. On this fixture it returns
    `unit:0` where the engine's pick is `unit:1`, so reaching for it would put a confident wrong
    answer into the one field an expired deferral uses to say what actually got built.
    """
    view = {k: v for k, v in WORLD_VIEW.items() if k != "native_choice"}
    client = TestClient(create_app(brain=DeferringBrain()))
    client.post("/decide", json=view)

    parked = client.get("/agent/pending").json()["pending"][0]
    assert parked["standing_action_id"] is None
    assert parked["standing_action"] is None
    # And it is still a usable deferral: the agent knows which base to come back to.
    assert parked["base_id"] == 7
