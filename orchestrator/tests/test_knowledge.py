"""The Quipu/Hank seam — ``docs/knowledge-architecture.md``.

Neither service is built. These pin the *contract* they will plug into, and in
particular the two properties that are expensive to retrofit: the knowledge
layer can never stall a turn, and what it contributed is always on the record.
"""

from __future__ import annotations

from pathlib import Path

from neural_amplifier.brain import Brain, ScriptedBrain
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.knowledge import Grounding, Ruling
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.replay import WorldViewStore


class Quipu:
    def __init__(self, *facts: str) -> None:
        self.facts = facts
        self.calls = 0

    def retrieve(self, world_view: WorldView) -> Grounding:
        self.calls += 1
        return Grounding(facts=self.facts)


class Hank:
    def __init__(self, ruling: Ruling) -> None:
        self.ruling = ruling
        self.seen: list[Orders] = []

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        self.seen.append(orders)
        return self.ruling


class Down:
    """Both roles, both broken."""

    def retrieve(self, world_view: WorldView) -> Grounding:
        raise ConnectionError("quipu unreachable")

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        raise ConnectionError("hank unreachable")


class Watching(ScriptedBrain):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[WorldView] = []

    def decide(self, world_view: WorldView) -> Orders:
        self.seen.append(world_view)
        return super().decide(world_view)


# --- degradation: knowledge is an optimisation, never a dependency ---------


def test_a_dead_quipu_does_not_stall_the_turn(thinker_base: WorldView) -> None:
    result = Orchestrator(ScriptedBrain(), retriever=Down()).decide(thinker_base)

    assert result.orders.choices  # the turn still happened
    assert result.record.degraded is False  # ...on a real decision, not a fallback
    assert result.record.knowledge.quipu_degraded is True


def test_a_dead_hank_allows_rather_than_blocks(thinker_base: WorldView) -> None:
    """A guard failure that silently blocked everything would stall the game to
    enforce a policy nobody could read. Engine legality still stands behind it."""
    result = Orchestrator(ScriptedBrain(), guard=Down()).decide(thinker_base)

    assert result.orders.choices
    assert result.record.knowledge.hank_verdict == "allow"
    assert result.record.knowledge.hank_degraded is True


def test_no_knowledge_layer_is_distinguishable_from_a_dead_one(
    thinker_base: WorldView,
) -> None:
    """Both give zero hits. Only the degraded flags say which happened."""
    absent = Orchestrator(ScriptedBrain()).decide(thinker_base).record.knowledge
    dead = Orchestrator(ScriptedBrain(), retriever=Down()).decide(thinker_base).record.knowledge

    assert absent.quipu_hits == dead.quipu_hits == 0
    assert absent.quipu_degraded is False
    assert dead.quipu_degraded is True
    assert absent.hank_verdict is None  # never consulted, vs an explicit "allow"


# --- retrieval -------------------------------------------------------------


def test_retrieved_facts_reach_the_brain(thinker_base: WorldView) -> None:
    brain = Watching()
    Orchestrator(brain, retriever=Quipu("Recycling Tanks needs Industrial Base")).decide(
        thinker_base
    )
    assert brain.seen[0].grounding == ["Recycling Tanks needs Industrial Base"]


def test_retrieval_cannot_widen_the_action_space(thinker_base: WorldView) -> None:
    """Grounding annotates the prompt. Legality stays the engine's word."""
    brain = Watching()
    before = thinker_base.action_ids()
    Orchestrator(brain, retriever=Quipu("a fact")).decide(thinker_base)
    assert brain.seen[0].action_ids() == before


def test_hits_are_counted_on_the_record(thinker_base: WorldView) -> None:
    result = Orchestrator(ScriptedBrain(), retriever=Quipu("one", "two", "three")).decide(
        thinker_base
    )
    assert result.record.knowledge.quipu_hits == 3


# --- the guard, and precedence --------------------------------------------


def test_the_guard_only_ever_sees_legal_orders(thinker_base: WorldView) -> None:
    """Precedence is order: engine legality > Hank deny. A hallucinated action
    is already gone before the guard runs, so the guard cannot be blamed for
    it — and cannot re-add it."""
    hank = Hank(Ruling())
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1"), Choice(action_id="ghost")])])
    Orchestrator(brain, guard=hank).decide(thinker_base)

    assert [c.action_id for c in hank.seen[0].choices] == ["a1"]


def test_a_deny_strips_the_named_choice(thinker_base: WorldView) -> None:
    hank = Hank(Ruling(verdict="deny", stripped=("a1",)))
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1"), Choice(action_id="a2")])])
    result = Orchestrator(brain, guard=hank).decide(thinker_base)

    assert [c.action_id for c in result.orders.choices] == ["a2"]
    assert result.record.knowledge.stripped == ["a1"]
    assert result.record.degraded is False


def test_a_warn_advises_but_removes_nothing(thinker_base: WorldView) -> None:
    hank = Hank(Ruling(verdict="warn", stripped=("a1",), advisories=("eco-damage risk",)))
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1")])])
    result = Orchestrator(brain, guard=hank).decide(thinker_base)

    assert [c.action_id for c in result.orders.choices] == ["a1"]
    assert result.record.knowledge.advisories == ["eco-damage risk"]


def test_denying_everything_degrades_rather_than_sending_an_empty_turn(
    thinker_base: WorldView,
) -> None:
    """An empty order set is indistinguishable from a stall, so it has to fall
    back — and say the guard was the cause, not the brain."""
    hank = Hank(Ruling(verdict="deny", stripped=("a1",)))
    # Repairs off, so this isolates the degrade path. With them on the brain would be re-asked
    # first, which is the subject of its own tests — see test_state_guard.py.
    brain = ScriptedBrain([Orders(choices=[Choice(action_id="a1")])])
    result = Orchestrator(brain, guard=hank, repair_attempts=0).decide(thinker_base)

    assert result.orders.degraded is True
    assert "guard denied every choice" in (result.record.degrade_reason or "")


def test_a_denial_the_brain_cannot_repair_still_degrades(thinker_base: WorldView) -> None:
    """The default path, end to end: denied, re-asked, denied again, fall back.

    The repair loop must not be able to turn a genuinely stuck decision into a hang, and the
    reason on the record has to say a repair was tried — otherwise a run that spent two brain
    calls looks identical to one that spent a single call and gave up.
    """
    hank = Hank(Ruling(verdict="deny", stripped=("a1",)))
    stubborn = ScriptedBrain(chooser=lambda _: Orders(choices=[Choice(action_id="a1")]))
    result = Orchestrator(stubborn, guard=hank, repair_attempts=1).decide(thinker_base)

    assert len(stubborn.calls) == 2
    assert result.orders.degraded is True
    assert "repair attempt" in (result.record.degrade_reason or "")


def test_a_degraded_brain_still_reports_why_it_degraded(thinker_base: WorldView) -> None:
    """The guard must not steal the blame when the brain is what failed."""
    hank = Hank(Ruling(verdict="deny", stripped=("a1",)))
    result = Orchestrator(
        ScriptedBrain(chooser=lambda _: Orders()), guard=hank, repair_attempts=0
    ).decide(thinker_base)

    assert "no legal choices" in (result.record.degrade_reason or "")


# --- provenance and replay -------------------------------------------------


def test_the_record_carries_the_knowledge_block(thinker_base: WorldView) -> None:
    """docs/observability.md §7 — "the brain was told this" has to be
    distinguishable from "the brain assumed this"."""
    result = Orchestrator(
        ScriptedBrain(), retriever=Quipu("a", "b"), guard=Hank(Ruling(verdict="allow"))
    ).decide(thinker_base)

    block = result.record.knowledge
    assert block.quipu_hits == 2
    assert block.hank_verdict == "allow"
    assert "knowledge" in result.record.model_dump()


def test_the_stored_view_is_what_the_record_addresses(
    thinker_base: WorldView, tmp_path: Path
) -> None:
    """Grounding is injected before the brain call, so it has to be inside the
    stored bytes too — otherwise world_view_hash addresses a file that does not
    exist and every replay silently reports missing_inputs."""
    store = WorldViewStore(tmp_path / "views")
    result = Orchestrator(ScriptedBrain(), store=store, retriever=Quipu("a fact")).decide(
        thinker_base
    )

    stored = store.get(result.record.world_view_hash)
    assert stored is not None
    assert stored.grounding == ["a fact"]


def test_latency_is_attributed_per_layer(thinker_base: WorldView) -> None:
    """quipu.latency and hank.latency are separate ops signals (§6) — a slow
    turn has to be attributable to one of them, not to 'the knowledge layer'."""
    brain: Brain = ScriptedBrain()
    block = (
        Orchestrator(brain, retriever=Quipu("a"), guard=Hank(Ruling()))
        .decide(thinker_base)
        .record.knowledge
    )
    assert block.quipu_latency_ms >= 0
    assert block.hank_latency_ms >= 0
