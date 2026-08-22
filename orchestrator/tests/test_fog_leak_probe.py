"""Fog of war on the KNOWLEDGE path: one faction's memories must not reach another's prompt.

The ruling (na-7bk): *"an agent deciding for faction F must not decide with knowledge of other
factions' decisions or private state"*, and *"a scoping rule without the leak-probe pair is a
diagram, not a gate."*

So every probe here comes in two halves, and the second half is what makes the first mean
anything:

  negative — plant a memory in faction 2's scope, decide for faction 4, assert it is ABSENT
  positive — decide for faction 2, assert the same memory is PRESENT

Absence on its own proves nothing. A retriever that is switched off, misconfigured, pointed at an
empty store, or broken outright passes every negative probe in this file perfectly. The positive
control is the only thing separating "the boundary held" from "nothing was retrieved at all" —
which is the exact mistake that made the fog design's own instructions insist on the pair.

Asserted on what the BRAIN was handed, not on what the store returned. The boundary is only real
where the decision is made; a store that filters correctly and a prompt that gets the facts
anyway is a leak with good intentions upstream of it.
"""

from __future__ import annotations

from typing import Any

from neural_amplifier.brain import Brain
from neural_amplifier.contract import Action, Orders, WorldView
from neural_amplifier.knowledge import Grounding
from neural_amplifier.memory import MemoryStore, RememberingRetriever, memory_scope
from neural_amplifier.orchestrator import Orchestrator

GAME = "g-probe"
FACTION_2_SECRET = (
    "faction 2 is massing probe teams near the isthmus [learned: 3 times, confidence 0.90]"
)
SHARED_TACTIC = "fungus yields rise with Centauri Ecology [learned: 9 times, confidence 0.95]"


class ScopedStore(MemoryStore):
    """A store whose contents are genuinely partitioned by scope.

    Deliberately a real partition rather than a stub returning a fixed list: a stub that ignores
    the scope argument would make the negative probe pass for the wrong reason, and this file
    exists precisely to refuse tests that pass for the wrong reason.
    """

    def __init__(self, by_scope: dict[str, list[str]]) -> None:
        super().__init__("http://memory.invalid")
        self.by_scope = by_scope
        self.asked: list[str] = []

    def recall(self, scope: str, limit: int = 10) -> list[str]:
        self.asked.append(scope)
        if not scope:
            return []
        # The union the decision loop is entitled to: this faction's own graph plus the shared,
        # game-agnostic one. Reached by being NAMED, never by the absence of a filter.
        from neural_amplifier.memory import DURABLE_GROUP

        out = list(self.by_scope.get(scope, []))
        out.extend(self.by_scope.get(DURABLE_GROUP, []))
        return out[:limit]


class CapturingBrain(Brain):
    """Answers nothing, and keeps the world view it was given. That view IS the evidence."""

    name = "capturing"

    def __init__(self) -> None:
        self.seen: list[WorldView] = []

    def decide(self, world_view: WorldView) -> Orders:
        self.seen.append(world_view)
        return Orders()


def view(faction_id: int, faction: str) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=7,
        faction=faction,
        faction_id=faction_id,
        surface_id="base.production",
        action_space=[Action(id="fac:4", action="Recycling Tanks")],
    )


class StubRulebook:
    """The rulebook plane, which is public: any faction could read the manual."""

    def retrieve(self, world_view: WorldView) -> Grounding:
        return Grounding(facts=("Recycling Tanks; Bonus Resources",), fact_ids=("fac:rt",))


def build(store: ScopedStore) -> tuple[Orchestrator, CapturingBrain]:
    retriever: Any = RememberingRetriever(StubRulebook(), store)
    retriever.bind_game(GAME)
    brain = CapturingBrain()
    return Orchestrator(brain=brain, game_id=GAME, retriever=retriever), brain


def facts_shown_to_the_brain(brain: CapturingBrain) -> tuple[str, ...]:
    """The grounding lines as the brain received them, on the world view it was handed.

    `WorldView.grounding` is `list[str]` — the lines, each optionally prefixed with its datalinks
    fact id. Read from the view rather than from the Grounding object the retriever returned,
    because the prompt is where a leak would actually land.
    """
    assert brain.seen, "the brain was never asked — this probe measured nothing"
    return tuple(brain.seen[-1].grounding or ())


def carries(shown: tuple[str, ...], fact: str) -> bool:
    """Substring, because a line may carry a fact-id prefix."""
    return any(fact in line for line in shown)


def a_store_with_a_secret() -> ScopedStore:
    return ScopedStore(
        {
            memory_scope(GAME, 2): [FACTION_2_SECRET],
            "memory:durable": [SHARED_TACTIC],
        }
    )


# --- the pair -----------------------------------------------------------------


def test_NEGATIVE_faction_4_does_not_see_faction_2s_memory() -> None:
    """The leak probe. Meaningless without the positive control below."""
    orchestrator, brain = build(a_store_with_a_secret())
    orchestrator.decide(view(4, "SPARTANS"))

    shown = facts_shown_to_the_brain(brain)
    assert not carries(shown, FACTION_2_SECRET), "faction 2's memory reached faction 4's prompt"


def test_POSITIVE_faction_2_does_see_its_own_memory() -> None:
    """The control that gives the probe above its meaning.

    Without this, a retriever that was simply off would pass the negative probe perfectly.
    """
    orchestrator, brain = build(a_store_with_a_secret())
    orchestrator.decide(view(2, "HIVE"))

    shown = facts_shown_to_the_brain(brain)
    assert carries(shown, FACTION_2_SECRET), (
        "faction 2 was denied its OWN memory — retrieval is broken"
    )


def test_the_shared_graph_is_shared_on_purpose() -> None:
    """Durable tactics are game-agnostic claims about SMAC mechanics — public-book knowledge.

    Both factions get them, and that is not a leak: it is the one crossing the ruling permits,
    and it is reached by naming `memory:durable` in the union rather than by having no filter at
    all. The distinction is invisible in the output and decisive in the mechanism, which is why
    it gets its own test.
    """
    for faction_id, name in ((2, "HIVE"), (4, "SPARTANS")):
        orchestrator, brain = build(a_store_with_a_secret())
        orchestrator.decide(view(faction_id, name))
        assert carries(facts_shown_to_the_brain(brain), SHARED_TACTIC)


def test_the_scope_asked_for_is_the_deciding_factions() -> None:
    """One layer below the prompt: the question put to the store names the right graph.

    Checked separately because the two can come apart in the direction that looks fine — asking
    globally and filtering afterwards produces an identical prompt today and leaks the moment
    anything downstream changes.
    """
    store = a_store_with_a_secret()
    orchestrator, _ = build(store)
    orchestrator.decide(view(4, "SPARTANS"))
    assert store.asked == [memory_scope(GAME, 4)]


def test_one_retriever_serving_two_factions_does_not_reuse_the_cache() -> None:
    """The leak that survives a correctly-scoped store.

    Recall is cached — durable tactics are written between games, so a round trip per decision
    would buy nothing. A cache shared across factions turns that optimisation into the leak this
    file exists to prevent: faction 2's lines handed to faction 4 without a query being issued at
    all, so the store never gets the chance to enforce anything.
    """
    store = a_store_with_a_secret()
    retriever: Any = RememberingRetriever(StubRulebook(), store)
    retriever.bind_game(GAME)
    brain = CapturingBrain()
    orchestrator = Orchestrator(brain=brain, game_id=GAME, retriever=retriever)

    orchestrator.decide(view(2, "HIVE"))
    assert carries(facts_shown_to_the_brain(brain), FACTION_2_SECRET)

    orchestrator.decide(view(4, "SPARTANS"))
    assert not carries(facts_shown_to_the_brain(brain), FACTION_2_SECRET), (
        "the cache carried faction 2's memory into faction 4's decision"
    )


# --- fail-closed ---------------------------------------------------------------


def test_an_unidentified_faction_recalls_nothing_rather_than_everything() -> None:
    """A decision whose faction we cannot name is exactly the one where guessing leaks."""
    store = a_store_with_a_secret()
    orchestrator, brain = build(store)
    anonymous = view(2, "HIVE").model_copy(update={"faction_id": None})
    orchestrator.decide(anonymous)

    assert store.asked == [], "an unscoped read was issued for a decision with no faction"
    assert not carries(facts_shown_to_the_brain(brain), FACTION_2_SECRET)
    # And the rulebook plane is untouched: it is public, and losing it would be a second bug
    # wearing the first one's clothes.
    assert carries(facts_shown_to_the_brain(brain), "Recycling Tanks; Bonus Resources")


def test_an_unbound_retriever_recalls_nothing() -> None:
    """Built before the Orchestrator that mints the game id, so it can legitimately be unbound.

    Unbound means no scope, and no scope means no read — not a global one.
    """
    store = a_store_with_a_secret()
    retriever: Any = RememberingRetriever(StubRulebook(), store)  # deliberately not bound
    brain = CapturingBrain()
    Orchestrator(brain=brain, game_id=GAME, retriever=retriever).decide(view(2, "HIVE"))

    assert store.asked == []
    assert not carries(facts_shown_to_the_brain(brain), FACTION_2_SECRET)


def test_recall_refuses_an_empty_scope_without_querying() -> None:
    """Fail-closed at the store, one layer below the retriever's own guard.

    Both layers, not either: the retriever's check is what a caller in the decision loop meets
    first, and this is what a caller that bypasses it meets.
    """
    asked: list[str] = []

    class Recording(MemoryStore):
        def _recall(self, limit: int, scopes: tuple[str, ...] | None) -> list[str]:
            asked.append(str(scopes))
            return ["should not be reachable"]

    store = Recording("http://memory.invalid")
    assert store.recall("") == []
    assert store.recall("   ") == []
    assert asked == [], "an empty scope reached the query builder"

    # The positive control, again: the same store DOES answer a real scope.
    assert store.recall(memory_scope(GAME, 2)) == ["should not be reachable"]
    assert asked == [str((memory_scope(GAME, 2), "memory:durable"))]


def test_the_unscoped_read_exists_but_has_to_be_asked_for_by_name() -> None:
    """Analysis genuinely needs every faction — a post-game review reads all six, and no decision
    is being made. It must not be reachable by forgetting an argument."""
    asked: list[str] = []

    class Recording(MemoryStore):
        def _recall(self, limit: int, scopes: tuple[str, ...] | None) -> list[str]:
            asked.append(str(scopes))
            return []

    Recording("http://memory.invalid").recall_across_factions()
    assert asked == ["None"], "the analysis read did not go out unscoped"


def test_scope_names_the_faction_not_just_the_game() -> None:
    """A per-game group puts all six factions in one bucket, which is not the boundary."""
    assert memory_scope("g1", 2) == "memory:game:g1:faction:2"
    assert memory_scope("g1", 2) != memory_scope("g1", 4)


def test_written_nodes_carry_their_scope_as_a_queryable_property() -> None:
    """`group_id` is an episode label no read can filter on — which is why the read path was
    global despite the groups looking like a partition. The property survives into the triples.
    """
    from neural_amplifier.memory import SCOPE_KEY, Extraction

    posted: list[dict[str, Any]] = []

    class Capturing(MemoryStore):
        def _post(self, payload: dict[str, Any]) -> None:
            posted.append(payload)

    store = Capturing("http://memory.invalid")
    store.write(
        Extraction(game_id=GAME, nodes=[{"name": "n1"}], tactics=[{"name": "t1"}]),
        faction_id=2,
    )

    game_episode = next(p for p in posted if p["group_id"] == memory_scope(GAME, 2))
    assert game_episode["nodes"][0][SCOPE_KEY] == memory_scope(GAME, 2)

    durable = next(p for p in posted if p["group_id"] == "memory:durable")
    assert durable["nodes"][0][SCOPE_KEY] == "memory:durable"


def test_an_unattributed_write_is_not_filed_under_a_guessed_faction() -> None:
    """A node whose faction is unknown cannot be scoped, and scoping it to a guess is how one
    faction's private observation reaches another's prompt."""
    from neural_amplifier.memory import Extraction, game_group

    posted: list[dict[str, Any]] = []

    class Capturing(MemoryStore):
        def _post(self, payload: dict[str, Any]) -> None:
            posted.append(payload)

    Capturing("http://memory.invalid").write(Extraction(game_id=GAME, nodes=[{"name": "n1"}]))
    assert posted[0]["group_id"] == game_group(GAME)
    assert ":faction:" not in posted[0]["group_id"]
