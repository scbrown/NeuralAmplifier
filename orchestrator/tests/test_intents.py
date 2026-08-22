"""Unit intents: the engine keeps the order, the graph keeps the reason (na-7bk slice 3).

A multi-turn goto already survives without us — `set_move_to` persists it. What does not survive
is WHY, and an unexplained order and a stale one look identical from a later turn. The safe
reading of an unexplained order is to leave it alone, which is exactly how a stale one survives.

So the tests here are about the reason being retrievable, being faction-private, and — most of
all — being able to expire. An intent that cannot come back for review is worse than none,
because it sits in every later prompt reading as a plan somebody is watching.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.contract import Action, WorldView
from neural_amplifier.intents import IntentError, UnitIntent
from neural_amplifier.intents import validate as validate_intent
from neural_amplifier.memory import MemoryStore, RememberingRetriever, memory_scope
from neural_amplifier.queued import Predicate
from neural_amplifier.service import create_app

GAME = "g-intent"


class RecordingStore(MemoryStore):
    """A memory store that keeps intents in memory, partitioned by scope.

    A real partition rather than a stub that ignores its scope argument: a stub would make the
    fog probe below pass for the wrong reason.
    """

    def __init__(self) -> None:
        super().__init__("http://memory.invalid")
        self.by_scope: dict[str, list[UnitIntent]] = {}

    @property
    def available(self) -> bool:
        return True

    def write_intent(self, intent: UnitIntent, scope: str) -> None:
        if not scope:
            return
        self.by_scope.setdefault(scope, []).append(intent)

    def recall_intents(self, scope: str, turn: int | None = None, limit: int = 10) -> list[str]:
        if not scope:
            return []
        out = []
        for i in self.by_scope.get(scope, []):
            if turn is not None and i.until_turn is not None and turn > i.until_turn:
                continue
            out.append(i.line())
        return out[:limit]

    def recall(self, scope: str, limit: int = 10) -> list[str]:
        return []


def fake_adapter(game_dir: Path, ok: bool = True, detail: str = "done") -> threading.Thread:
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
                            "turn": 40,
                            "halted": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def app_with_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, RecordingStore]:
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    app = create_app()
    store = RecordingStore()
    retriever: Any = RememberingRetriever(None, store)
    retriever.bind_game(GAME)
    app.state.orchestrator.retriever = retriever
    return TestClient(app), store


INTENT = {
    "faction_id": 2,
    "unit_id": 12,
    "goal": "reach the isthmus and hold the land bridge",
    "rationale": "it is the only land route between our continent and the Hive",
    "until_turn": 60,
    "triggers": [{"metric": "military_units", "comparator": "at_least", "target": 4}],
}


# --- recording -----------------------------------------------------------------


def test_a_confirmed_order_records_why_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = app_with_store(tmp_path, monkeypatch)
    fake_adapter(tmp_path, ok=True, detail="veh 12 -> (40,21) rc=1")

    got = client.post(
        "/order", json={"verb": "move", "args": [12, 40, 21], "intent": INTENT}
    ).json()

    assert got["status"] == "ok"
    assert got["intent"]["recorded"] is True
    assert store.by_scope[memory_scope(GAME, 2)][0].unit_id == 12


def test_an_unconfirmed_order_records_no_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remembered reason for an order that never landed describes a plan no unit is executing.

    The engine keeps the goto; if it never got the goto, there is nothing to explain.
    """
    client, store = app_with_store(tmp_path, monkeypatch)
    # No adapter: the command is written and nothing answers it.
    got = client.post(
        "/order",
        json={"verb": "move", "args": [12, 40, 21], "timeout_s": 0.2, "intent": INTENT},
    ).json()

    assert got["status"] != "ok"
    assert got["intent"]["recorded"] is False
    assert store.by_scope == {}


def test_an_order_with_no_intent_is_unaffected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Intents are an addition to ordering, never a precondition for it."""
    client, store = app_with_store(tmp_path, monkeypatch)
    fake_adapter(tmp_path, ok=True)
    got = client.post("/order", json={"verb": "move", "args": [12, 40, 21]}).json()

    assert got["status"] == "ok"
    assert "intent" not in got
    assert store.by_scope == {}


def test_a_bad_intent_is_refused_before_the_order_is_issued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 422 must reach the agent while it still holds the WHOLE act.

    Validated after issuing, a refused annotation would throw away the confirmation of an order
    the game already ran — the caller would see an error for a move that happened. No adapter
    runs here, so the assertion that no command file exists is the assertion that nothing was
    sent at all.
    """
    client, store = app_with_store(tmp_path, monkeypatch)
    resp = client.post(
        "/order",
        json={"verb": "move", "args": [12, 40, 21], "intent": {**INTENT, "until_turn": None}},
    )
    assert resp.status_code == 422
    assert not (tmp_path / "na-command").exists(), "the order must not have been issued"
    assert store.by_scope == {}


# --- batches: an army ordered and explained in one round trip (na-7bk) ---------------


def batch_adapter(game_dir: Path, oks: list[bool], dropped: int = 0) -> None:
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
                            "dropped": dropped,
                            "ok": all(r["ok"] for r in results) and dropped == 0,
                            "turn": 40,
                            "halted": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    threading.Thread(target=run, daemon=True).start()


def test_each_confirmed_order_in_a_batch_records_its_own_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = app_with_store(tmp_path, monkeypatch)
    batch_adapter(tmp_path, oks=[True, True])

    got = client.post(
        "/order",
        json={
            "orders": [
                {"verb": "move", "args": [12, 40, 21], "intent": INTENT},
                {"verb": "move", "args": [13, 41, 21], "intent": {**INTENT, "unit_id": 13}},
            ]
        },
    ).json()

    assert got["status"] == "ok"
    assert [n["recorded"] for n in got["intents"]] == [True, True]
    assert sorted(i.unit_id for i in store.by_scope[memory_scope(GAME, 2)]) == [12, 13]


def test_only_the_confirmed_half_of_a_batch_records_its_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmation is per ORDER, not per envelope. A batch that half-worked reports `refused`
    overall, and the order that DID land still deserves its reason — while the one that did not
    must not leave a remembered plan no unit is executing."""
    client, store = app_with_store(tmp_path, monkeypatch)
    batch_adapter(tmp_path, oks=[True, False])

    got = client.post(
        "/order",
        json={
            "orders": [
                {"verb": "move", "args": [12, 40, 21], "intent": INTENT},
                {"verb": "move", "args": [13, 9, 9], "intent": {**INTENT, "unit_id": 13}},
            ]
        },
    ).json()

    assert got["status"] == "refused"
    by_order = {n["order"]: n for n in got["intents"]}
    assert by_order[0]["recorded"] is True
    assert by_order[1]["recorded"] is False
    recorded = store.by_scope[memory_scope(GAME, 2)]
    assert [i.unit_id for i in recorded] == [12]


def test_a_bad_intent_refuses_the_whole_batch_before_anything_is_issued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mid-batch is the worst possible moment for a 422: half the army has already moved. So a
    refusable intent anywhere in the list refuses the list, while the agent still holds it."""
    client, store = app_with_store(tmp_path, monkeypatch)
    resp = client.post(
        "/order",
        json={
            "orders": [
                {"verb": "move", "args": [12, 40, 21], "intent": INTENT},
                {"verb": "move", "args": [13, 41, 21], "intent": {**INTENT, "until_turn": None}},
            ]
        },
    )
    assert resp.status_code == 422
    assert "horizon" in resp.json()["detail"]
    assert not (tmp_path / "na-command").exists(), "nothing may have been issued"
    assert store.by_scope == {}


def test_a_batch_answered_without_per_order_results_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed on the envelope. An adapter that answers `ok` without per-order entries — an
    old DLL that read only the first line is the live case — has confirmed no individual order,
    and the envelope's own `ok` says everything worked, not which things did."""
    client, store = app_with_store(tmp_path, monkeypatch)
    fake_adapter(tmp_path, ok=True)  # the single-envelope shape: no `results` list

    got = client.post(
        "/order",
        json={"orders": [{"verb": "move", "args": [12, 40, 21], "intent": INTENT}]},
    ).json()

    assert got["intents"][0]["recorded"] is False
    assert store.by_scope == {}


def test_a_dropped_orders_intent_is_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An order past the adapter's per-tick cap was NOT executed; it has no result entry, and no
    result entry means unconfirmed, never 'probably fine'."""
    client, store = app_with_store(tmp_path, monkeypatch)
    batch_adapter(tmp_path, oks=[True], dropped=1)

    got = client.post(
        "/order",
        json={
            "orders": [
                {"verb": "move", "args": [12, 40, 21], "intent": INTENT},
                {"verb": "move", "args": [13, 41, 21], "intent": {**INTENT, "unit_id": 13}},
            ]
        },
    ).json()

    assert got["dropped"] == 1
    by_order = {n["order"]: n for n in got["intents"]}
    assert by_order[0]["recorded"] is True
    assert by_order[1]["recorded"] is False
    assert [i.unit_id for i in store.by_scope[memory_scope(GAME, 2)]] == [12]


def test_a_body_level_intent_on_a_batch_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch-level intent names no order, and guessing which one it meant would attach a
    reason to a move nobody explained."""
    client, store = app_with_store(tmp_path, monkeypatch)
    resp = client.post(
        "/order",
        json={"orders": [{"verb": "move", "args": [12, 40, 21]}], "intent": INTENT},
    )
    assert resp.status_code == 422
    assert not (tmp_path / "na-command").exists()
    assert store.by_scope == {}


# --- the reason comes back -------------------------------------------------------


def view(faction_id: int, turn: int) -> dict:
    return {
        "schema_version": "0.1",
        "engine": "thinker",
        "scope": "base",
        "surface_id": "base.production",
        "turn": turn,
        "faction": "HIVE" if faction_id == 2 else "SPARTANS",
        "faction_id": faction_id,
        "metrics": {"military_units": 9},
        "action_space": [{"id": "unit:0", "action": "Colony Pod", "cost": 30}],
    }


def test_the_intent_is_in_front_of_a_later_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No new prompt plumbing: recall already runs before every brain call, so an intent written
    at order time surfaces on the next decision for that faction."""
    client, _ = app_with_store(tmp_path, monkeypatch)
    fake_adapter(tmp_path, ok=True)
    client.post("/order", json={"verb": "move", "args": [12, 40, 21], "intent": INTENT})

    client.post("/decide", json=view(2, 41))

    grounding = client.app.state.orchestrator.retriever.retrieve(  # type: ignore[attr-defined]
        WorldView(
            engine="thinker",
            scope="base",
            turn=41,
            faction="HIVE",
            faction_id=2,
            surface_id="base.production",
            action_space=[Action(id="unit:0", action="Colony Pod")],
        )
    )
    assert any("isthmus" in f for f in grounding.facts), "the reason must come back with the unit"


def test_an_intent_is_not_cached_past_the_turn_it_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tactics are cached because they are written between games. An intent is written DURING one,
    by this same agent, minutes ago — caching would make a plan formed on turn 40 invisible on
    turn 41, which is precisely the case the mechanism exists for."""
    client, store = app_with_store(tmp_path, monkeypatch)
    retriever = client.app.state.orchestrator.retriever  # type: ignore[attr-defined]

    early = retriever.retrieve(
        WorldView(
            engine="thinker",
            scope="base",
            turn=40,
            faction="HIVE",
            faction_id=2,
            surface_id="base.production",
            action_space=[Action(id="unit:0", action="X")],
        )
    )
    assert not early.facts

    store.write_intent(
        UnitIntent(unit_id=12, goal="hold the isthmus", until_turn=60), memory_scope(GAME, 2)
    )

    later = retriever.retrieve(
        WorldView(
            engine="thinker",
            scope="base",
            turn=41,
            faction="HIVE",
            faction_id=2,
            surface_id="base.production",
            action_space=[Action(id="unit:0", action="X")],
        )
    )
    assert any("isthmus" in f for f in later.facts), "a plan formed this game must be visible now"


def test_an_expired_intent_leaves_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not deleted from the graph — an intent is not WRONG when it expires, it is finished, and
    the record of what was intended is worth keeping. It just must not sit in a later prompt
    reading as though it still applied."""
    client, store = app_with_store(tmp_path, monkeypatch)
    store.write_intent(
        UnitIntent(unit_id=12, goal="hold the isthmus", until_turn=45), memory_scope(GAME, 2)
    )
    retriever = client.app.state.orchestrator.retriever  # type: ignore[attr-defined]

    def facts_at(turn: int) -> tuple[str, ...]:
        return retriever.retrieve(
            WorldView(
                engine="thinker",
                scope="base",
                turn=turn,
                faction="HIVE",
                faction_id=2,
                surface_id="base.production",
                action_space=[Action(id="unit:0", action="X")],
            )
        ).facts

    assert any("isthmus" in f for f in facts_at(45)), "positive control: live before the horizon"
    assert not any("isthmus" in f for f in facts_at(46))
    # And it is still in the graph — expiry is a prompt rule, not a deletion.
    assert store.by_scope[memory_scope(GAME, 2)]


# --- fog: an intent is faction-private BY DEFINITION -------------------------------


def test_NEGATIVE_another_faction_cannot_recall_this_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'I am massing rovers at the isthmus by turn 60' is precisely what an opponent must not
    recall. Meaningless without the positive control below."""
    client, store = app_with_store(tmp_path, monkeypatch)
    store.write_intent(
        UnitIntent(unit_id=12, goal="hold the isthmus", until_turn=60), memory_scope(GAME, 2)
    )
    retriever = client.app.state.orchestrator.retriever  # type: ignore[attr-defined]

    other = retriever.retrieve(
        WorldView(
            engine="thinker",
            scope="base",
            turn=41,
            faction="SPARTANS",
            faction_id=4,
            surface_id="base.production",
            action_space=[Action(id="unit:0", action="X")],
        )
    )
    assert not any("isthmus" in f for f in other.facts)


def test_POSITIVE_the_owning_faction_can(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = app_with_store(tmp_path, monkeypatch)
    store.write_intent(
        UnitIntent(unit_id=12, goal="hold the isthmus", until_turn=60), memory_scope(GAME, 2)
    )
    retriever = client.app.state.orchestrator.retriever  # type: ignore[attr-defined]

    mine = retriever.retrieve(
        WorldView(
            engine="thinker",
            scope="base",
            turn=41,
            faction="HIVE",
            faction_id=2,
            surface_id="base.production",
            action_space=[Action(id="unit:0", action="X")],
        )
    )
    assert any("isthmus" in f for f in mine.facts)


def test_the_faction_comes_from_the_caller_not_from_the_unit_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Units are numbered in one engine-wide sequence, so inferring the faction from a unit id is
    a coincidence away from writing one faction's private plan into another's graph."""
    client, _ = app_with_store(tmp_path, monkeypatch)
    fake_adapter(tmp_path, ok=True)
    resp = client.post(
        "/order",
        json={"verb": "move", "args": [12, 40, 21], "intent": {**INTENT, "faction_id": None}},
    )
    assert resp.status_code == 422
    assert "faction_id" in resp.json()["detail"]


# --- refusals: an intent nothing can bring back is worse than none -------------------


def test_an_intent_with_no_horizon_is_refused() -> None:
    with pytest.raises(IntentError, match="until_turn"):
        validate_intent(
            UnitIntent(
                unit_id=1,
                goal="go east",
                triggers=[Predicate(metric="military_units", comparator="at_least", target=1)],
            )
        )


def test_an_intent_with_no_triggers_is_refused() -> None:
    """Nothing about the board could make it wrong, so it is a note rather than a plan."""
    with pytest.raises(IntentError, match="predicate"):
        validate_intent(UnitIntent(unit_id=1, goal="go east", until_turn=60))


def test_a_trigger_outside_the_measured_vocabulary_is_refused() -> None:
    """Same rule as a queued answer's predicate, and it costs more here: three of the four
    triggers the design names (enemy_near, tile ownership, unit damage) need data the
    orchestrator does not have, by design. Refused rather than accepted-and-never-fired."""
    with pytest.raises(IntentError, match="unknown metric"):
        validate_intent(
            UnitIntent(
                unit_id=1,
                goal="go east",
                until_turn=60,
                triggers=[Predicate(metric="enemy_near", comparator="at_most", target=3)],
            )
        )


def test_a_refused_intent_is_reported_over_http_while_the_agent_still_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = app_with_store(tmp_path, monkeypatch)
    fake_adapter(tmp_path, ok=True)
    resp = client.post(
        "/order",
        json={
            "verb": "move",
            "args": [12, 40, 21],
            "intent": {**INTENT, "until_turn": None},
        },
    )
    assert resp.status_code == 422
    assert "horizon" in resp.json()["detail"]


# --- review ------------------------------------------------------------------------


def test_review_is_due_when_a_trigger_stops_holding() -> None:
    intent = UnitIntent(
        unit_id=12,
        goal="hold the isthmus",
        until_turn=60,
        triggers=[Predicate(metric="military_units", comparator="at_least", target=4)],
    )
    healthy = WorldView(
        engine="thinker",
        scope="base",
        turn=41,
        faction="HIVE",
        surface_id="s",
        metrics={"military_units": 9},
        action_space=[],
    )
    due, why = intent.review_due(healthy)
    assert not due, "positive control: an intact escort does not need review"

    mauled = WorldView(
        engine="thinker",
        scope="base",
        turn=41,
        faction="HIVE",
        surface_id="s",
        metrics={"military_units": 1},
        action_space=[],
    )
    due, why = intent.review_due(mauled)
    assert due
    assert "military_units is 1" in " ".join(why)


def test_review_is_due_when_the_horizon_passes() -> None:
    intent = UnitIntent(
        unit_id=12,
        goal="hold the isthmus",
        until_turn=45,
        triggers=[Predicate(metric="military_units", comparator="at_least", target=1)],
    )
    late = WorldView(
        engine="thinker",
        scope="base",
        turn=46,
        faction="HIVE",
        surface_id="s",
        metrics={"military_units": 9},
        action_space=[],
    )
    due, why = intent.review_due(late)
    assert due
    assert "horizon passed" in " ".join(why)


def test_a_missing_metric_makes_review_due_rather_than_passing_silently() -> None:
    """Fail-closed, same as a queued predicate: absence of evidence is not evidence the plan is
    still sound."""
    intent = UnitIntent(
        unit_id=12,
        goal="hold the isthmus",
        until_turn=60,
        triggers=[Predicate(metric="military_units", comparator="at_least", target=4)],
    )
    silent = WorldView(
        engine="thinker",
        scope="base",
        turn=41,
        faction="HIVE",
        surface_id="s",
        metrics={},
        action_space=[],
    )
    due, why = intent.review_due(silent)
    assert due
    assert "not reported" in " ".join(why)
