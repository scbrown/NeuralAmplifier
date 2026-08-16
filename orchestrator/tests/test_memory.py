"""Learned memory — `memory.py`, K3.

The roadmap sets one exit criterion and nothing softer: **a tactic learned in game N surfaces in
game N+1**. So the test that matters is the round trip, and the live lane below is where it is
actually made — everything above it is about not learning the wrong thing.

Two exclusions carry most of the value and neither is obvious from the outside. A
deterministic-tier record says what the engine would have done anyway, so learning from it
teaches the brain the fallback's habits and then reports the agreement as evidence the brain was
right. A degraded record is worse: it exists *because* the brain could not answer, so the chosen
action is the fallback's, and a tactic built from it is a habit learned from the brain's absence.

Verified live against quipu 0.3.23 with quipu-server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from neural_amplifier.contract import Action, WorldView
from neural_amplifier.knowledge import Grounding
from neural_amplifier.memory import (
    DURABLE_GROUP,
    TACTIC_THRESHOLD,
    Extraction,
    MemoryStore,
    RememberingRetriever,
    episodes,
    game_group,
)

MEMORY_URL = os.environ.get("NA_MEMORY_QUIPU_URL", "")


def record(**over: Any) -> dict[str, Any]:
    base = {
        "game_id": "game-N",
        "turn": 10,
        "faction": "GAIANS",
        "engine": "thinker",
        "surface_id": "base.production",
        "scope": "base",
        "tier": "llm",
        "world_view_hash": "sha256:x",
        "action_space_size": 5,
        "degraded": False,
        "chosen": [{"action_id": "fac:4", "reason": "economy first"}],
    }
    return {**base, **over}


def write_log(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "decisions.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def repeated(n: int, **over: Any) -> list[dict[str, Any]]:
    return [record(turn=10 + i, **over) for i in range(n)]


# --- what becomes a memory --------------------------------------------------


def test_a_repeated_choice_becomes_a_tactic(tmp_path: Path) -> None:
    out = episodes(write_log(tmp_path, repeated(TACTIC_THRESHOLD)))

    (tactic,) = out.tactics
    assert tactic["type"] == "Tactic"
    assert tactic["properties"]["trigger"] == "base.production"
    assert tactic["properties"]["action"] == "fac:4"
    assert tactic["properties"]["observations"] == TACTIC_THRESHOLD


def test_one_choice_is_not_a_tactic(tmp_path: Path) -> None:
    """Two is a coincidence. The threshold is what stops a single turn becoming doctrine."""
    assert episodes(write_log(tmp_path, repeated(TACTIC_THRESHOLD - 1))).tactics == []


def test_the_deterministic_tier_teaches_nothing(tmp_path: Path) -> None:
    """It says what the engine would have done anyway. Learning from it would teach the brain
    the fallback's habits and then read the agreement as evidence the brain was right."""
    rows = repeated(TACTIC_THRESHOLD + 2, tier="deterministic")
    out = episodes(write_log(tmp_path, rows))

    assert out.tactics == []
    assert [n["type"] for n in out.nodes] == ["Game"]  # the game itself, and no decisions


def test_a_degraded_decision_teaches_nothing(tmp_path: Path) -> None:
    """Sharper than the tier exclusion: the record exists BECAUSE the brain could not answer,
    so the chosen action is the fallback's. A tactic built from these is a habit learned from
    the brain's absence."""
    rows = repeated(TACTIC_THRESHOLD + 2, degraded=True, degrade_reason="timeout")
    assert episodes(write_log(tmp_path, rows)).tactics == []


def test_confidence_stays_well_short_of_certainty(tmp_path: Path) -> None:
    """A habit observed many times inside ONE game is not a law of the game. The cap is what
    keeps a tactic a claim rather than an instruction."""
    out = episodes(write_log(tmp_path, repeated(40)))
    assert out.tactics[0]["properties"]["confidence"] <= 0.6


def test_the_game_node_records_the_run(tmp_path: Path) -> None:
    out = episodes(write_log(tmp_path, repeated(3)))
    (game,) = [n for n in out.nodes if n["type"] == "Game"]

    assert game["properties"]["faction"] == "GAIANS"
    assert game["properties"]["decisions"] == 3
    assert out.game_id == "game-N"


def test_decisions_are_linked_to_their_game(tmp_path: Path) -> None:
    out = episodes(write_log(tmp_path, repeated(3)))
    assert out.edges
    assert all(e["relation"] == "inGame" for e in out.edges)
    assert all(e["target"] == f"mem:game/{out.game_id}" for e in out.edges)


def test_a_truncated_final_line_costs_that_decision_and_no_other(tmp_path: Path) -> None:
    """The log is appended as the game runs, so a run killed mid-write leaves one. Raising
    would cost the whole game's memory, which is the trade this refuses."""
    path = tmp_path / "decisions.jsonl"
    good = "".join(json.dumps(r) + "\n" for r in repeated(3))
    path.write_text(good + json.dumps(record(turn=99))[:40], encoding="utf-8")

    out = episodes(path)
    assert out.tactics[0]["properties"]["observations"] == 3


def test_an_empty_or_missing_log_yields_nothing(tmp_path: Path) -> None:
    assert not episodes(tmp_path / "nope.jsonl")
    assert not episodes(write_log(tmp_path, []))


# --- the bitemporal split ---------------------------------------------------


def test_the_two_planes_are_written_to_different_groups(tmp_path: Path) -> None:
    """Per-game memories are archived when the game ends; durable tactics outlive it. One group
    would make a lesson from one game indistinguishable from a lesson from forty."""
    posted: list[dict[str, Any]] = []

    class Recording(MemoryStore):
        def _post(self, payload: dict[str, Any]) -> None:
            posted.append(payload)

    store = Recording("http://memory.invalid")
    store.write(episodes(write_log(tmp_path, repeated(TACTIC_THRESHOLD))))

    groups = {p["group_id"]: p for p in posted}
    assert set(groups) == {game_group("game-N"), DURABLE_GROUP}
    assert all(n["type"] == "Tactic" for n in groups[DURABLE_GROUP]["nodes"])
    assert not any(n["type"] == "Tactic" for n in groups[game_group("game-N")]["nodes"])


def test_no_store_configured_writes_nothing_rather_than_raising() -> None:
    assert MemoryStore("").write(Extraction(game_id="g", nodes=[{"name": "n"}])) == {
        "game": 0,
        "durable": 0,
    }
    assert MemoryStore("").recall() == []


# --- recall -----------------------------------------------------------------


class FakeStore(MemoryStore):
    def __init__(self, lines: list[str]) -> None:
        super().__init__("http://memory.invalid")
        self.lines = lines
        self.calls = 0

    def recall(self, limit: int = 10) -> list[str]:
        self.calls += 1
        return self.lines


def view() -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="GAIANS",
        surface_id="base.production",
        action_space=[Action(id="fac:4", action="Recycling Tanks")],
    )


class StubRetriever:
    def retrieve(self, world_view: WorldView) -> Grounding:
        return Grounding(facts=("Recycling Tanks; Bonus Resources",), fact_ids=("fac:rt",))


def test_tactics_are_appended_behind_the_rulebook() -> None:
    """The rulebook facts are about the options actually on the table; a tactic is a standing
    habit. Putting the weaker claim first would give it the stronger position."""
    retriever = RememberingRetriever(StubRetriever(), FakeStore(["prefer fac:4 [x3]"]))
    grounding = retriever.retrieve(view())

    assert grounding.facts == ("Recycling Tanks; Bonus Resources", "prefer fac:4 [x3]")


def test_a_tactic_is_never_given_a_citable_id() -> None:
    """`fact_ids` are datalinks nodes a citation can be resolved against. A tactic has no such
    node, and minting one would make CitationGuard resolve it to nothing and report a fabricated
    citation on a fact we invented ourselves."""
    retriever = RememberingRetriever(StubRetriever(), FakeStore(["prefer fac:4 [x3]"]))
    assert retriever.retrieve(view()).fact_ids == ("fac:rt",)


def test_memory_works_with_no_rulebook_retrieval_at_all() -> None:
    """Worth having in an ungrounded run, and arguably worth more — the brain has less else."""
    grounding = RememberingRetriever(None, FakeStore(["prefer fac:4 [x3]"])).retrieve(view())
    assert grounding.facts == ("prefer fac:4 [x3]",)


def test_recall_happens_once_per_process() -> None:
    """Durable tactics are written BETWEEN games, not during one, so asking per decision is a
    round trip per turn for an answer that cannot have changed."""
    store = FakeStore(["prefer fac:4 [x3]"])
    retriever = RememberingRetriever(StubRetriever(), store)
    for _ in range(5):
        retriever.retrieve(view())

    assert store.calls == 1


def test_nothing_learned_leaves_the_grounding_untouched() -> None:
    retriever = RememberingRetriever(StubRetriever(), FakeStore([]))
    assert retriever.retrieve(view()).facts == ("Recycling Tanks; Bonus Resources",)


# --- the exit criterion, against a real store -------------------------------

live = pytest.mark.skipif(
    not MEMORY_URL, reason="set NA_MEMORY_QUIPU_URL to a running quipu-server"
)


@live
def test_a_tactic_learned_in_game_n_surfaces_in_game_n_plus_one(tmp_path: Path) -> None:
    """K3's exit criterion, verbatim, and the only test here that proves the feature.

    Game N is played and extracted; game N+1 asks what is known and is told. Nothing between
    them shares a process variable — the tactic goes through a real store and comes back as a
    prompt line.
    """
    store = MemoryStore(MEMORY_URL)
    rows = repeated(TACTIC_THRESHOLD + 1, game_id="game-exit-test")
    rows[0] = {**rows[0], "surface_id": "faction.tech", "chosen": [{"action_id": "tech:12"}]}

    written = store.write(episodes(write_log(tmp_path, rows)))
    assert written["durable"] >= 1

    recalled = " ".join(store.recall(limit=50))
    assert "base.production" in recalled
    assert "prefer fac:4" in recalled
