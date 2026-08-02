"""The agent-as-brain seam: queue, doorbell, and the blocking decision.

Two threads meet on every one of these tests, because that is what the design is: a game thread
blocked inside ``Orchestrator.decide`` and an agent thread answering from somewhere else. A
single-threaded test of this would pass while the real thing deadlocked.
"""

from __future__ import annotations

import threading
import time

import pytest

from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.brain import BrainError
from neural_amplifier.contract import Action, Choice, Orders, WorldView
from neural_amplifier.doorbell import Doorbell
from neural_amplifier.pending import (
    DecisionQueue,
    NotClaimable,
    QueueFull,
    Unanswered,
)


def world_view(surface: str = "base.production", turn: int = 1) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=turn,
        faction="Gaians",
        surface_id=surface,
        action_space=[
            Action(id="unit:0", action="Colony Pod"),
            Action(id="facility:4", action="Recycling Tanks"),
        ],
    )


class SilentDoorbell(Doorbell):
    """A doorbell that records rings instead of touching tmux."""

    def __init__(self) -> None:
        super().__init__(target="test", enabled=True)
        self.rings: list[tuple[str, str | None]] = []

    def ring(self, decision_id: str, surface_id: str | None = None) -> bool:
        self.rings.append((decision_id, surface_id))
        return True


# ----------------------------------------------------------------------- queue


def test_post_then_claim_then_answer() -> None:
    queue = DecisionQueue()
    pending = queue.post(world_view())

    claimed = queue.claim(agent="tester")
    assert claimed is not None
    assert claimed.id == pending.id
    assert claimed.claimed_by == "tester"

    queue.answer(pending.id, Orders(choices=[Choice(action_id="unit:0")]))
    orders = queue.await_answer(pending, timeout=1)
    assert orders.choices[0].action_id == "unit:0"


def test_a_claimed_decision_is_not_offered_twice() -> None:
    """Two attached agents must not both answer the same decision.

    The second agent is told there is nothing waiting rather than handed a duplicate — losing
    that race silently is how two orders get submitted for one build.
    """
    queue = DecisionQueue()
    queue.post(world_view())
    assert queue.claim(agent="first") is not None
    assert queue.claim(agent="second") is None


def test_answering_twice_is_refused() -> None:
    queue = DecisionQueue()
    pending = queue.post(world_view())
    queue.claim()
    queue.answer(pending.id, Orders(choices=[Choice(action_id="unit:0")]))
    with pytest.raises(NotClaimable, match="already answered"):
        queue.answer(pending.id, Orders(choices=[Choice(action_id="facility:4")]))


def test_answering_an_unknown_decision_is_refused() -> None:
    """The message matters: it goes back to the model as a tool result to act on."""
    queue = DecisionQueue()
    with pytest.raises(NotClaimable, match="no decision"):
        queue.answer("base.production-99", Orders())


def test_timeout_abandons_and_refuses_a_late_answer() -> None:
    """A turn that moved on must not accept orders for the decision it moved past."""
    queue = DecisionQueue()
    pending = queue.post(world_view())
    with pytest.raises(Unanswered):
        queue.await_answer(pending, timeout=0.05)
    with pytest.raises(NotClaimable, match="abandoned"):
        queue.answer(pending.id, Orders(choices=[Choice(action_id="unit:0")]))


def test_queue_is_bounded() -> None:
    """Depth is a bug signal, not backpressure: one pending decision is one blocked worker."""
    queue = DecisionQueue(max_depth=2)
    queue.post(world_view())
    queue.post(world_view())
    with pytest.raises(QueueFull):
        queue.post(world_view())


def test_settled_decisions_stop_counting_against_the_bound() -> None:
    queue = DecisionQueue(max_depth=1)
    first = queue.post(world_view())
    queue.answer(first.id, Orders(choices=[Choice(action_id="unit:0")]))
    queue.await_answer(first, timeout=1)
    queue.post(world_view())  # must not raise


def test_claim_can_wait_for_a_decision_to_arrive() -> None:
    """The no-doorbell path: an agent that polls blocks instead of spinning."""
    queue = DecisionQueue()
    seen: list[str] = []

    def poster() -> None:
        time.sleep(0.1)
        queue.post(world_view())

    threading.Thread(target=poster, daemon=True).start()
    claimed = queue.claim(wait=3.0)
    assert claimed is not None
    seen.append(claimed.id)
    assert seen


def test_abandon_releases_a_waiting_game_thread() -> None:
    queue = DecisionQueue()
    pending = queue.post(world_view())
    threading.Timer(0.05, lambda: queue.abandon(pending.id, "game gave up")).start()
    with pytest.raises(Unanswered, match="game gave up"):
        queue.await_answer(pending, timeout=3)


# ----------------------------------------------------------------- agent brain


def test_agent_brain_blocks_until_an_agent_answers() -> None:
    """The whole pivot in one test: the brain waits, an agent answers, orders come back."""
    doorbell = SilentDoorbell()
    brain = AgentBrain(doorbell=doorbell, timeout=5)
    result: dict[str, Orders] = {}

    def game_thread() -> None:
        result["orders"] = brain.decide(world_view())

    game = threading.Thread(target=game_thread)
    game.start()

    # The doorbell is what tells the agent to look, so waiting on it is the honest way to
    # synchronise here — a sleep would pass even if the ring never happened.
    deadline = time.monotonic() + 3
    while not doorbell.rings and time.monotonic() < deadline:
        time.sleep(0.01)
    assert doorbell.rings, "the brain must ring before it waits, or nothing knows to answer"

    claimed = brain.queue.claim()
    assert claimed is not None
    brain.queue.answer(claimed.id, Orders(choices=[Choice(action_id="facility:4")]))

    game.join(timeout=3)
    assert not game.is_alive()
    assert result["orders"].choices[0].action_id == "facility:4"


def test_the_doorbell_names_the_surface_but_carries_no_game_data() -> None:
    """A nudge is a nudge. Base names are player-editable, so nothing from the world view
    may ride on a command line."""
    doorbell = SilentDoorbell()
    brain = AgentBrain(doorbell=doorbell, timeout=0.05)
    with pytest.raises(BrainError):
        brain.decide(world_view(surface="base.hurry"))
    (decision_id, surface_id) = doorbell.rings[0]
    assert surface_id == "base.hurry"
    assert decision_id.startswith("base.hurry-")


def test_a_silent_agent_degrades_instead_of_hanging() -> None:
    """`NA_AGENT_TIMEOUT` is the unattended-run escape from the blocking default."""
    brain = AgentBrain(doorbell=SilentDoorbell(), timeout=0.05)
    with pytest.raises(BrainError, match="no answer within"):
        brain.decide(world_view())


def test_a_full_queue_degrades_rather_than_piling_on() -> None:
    brain = AgentBrain(queue=DecisionQueue(max_depth=1), doorbell=SilentDoorbell(), timeout=0.05)
    brain.queue.post(world_view())
    with pytest.raises(BrainError, match="queue full"):
        brain.decide(world_view())


def test_missing_doorbell_is_not_an_error() -> None:
    """No tmux target configured is a valid setup — a polling agent needs no doorbell."""
    brain = AgentBrain(doorbell=Doorbell(target="", enabled=False), timeout=0.05)
    with pytest.raises(BrainError, match="no answer within"):
        brain.decide(world_view())


# ------------------------------------------------------- the engine's deadline (na-t3h)


def test_the_engine_deadline_bounds_a_brain_configured_to_wait_forever() -> None:
    """The shipped default is `NA_AGENT_TIMEOUT` unset, which means wait forever — and that is
    the configuration na-t3h was measured in. Waiting forever is only defensible while the game
    is still waiting too; once the adapter has applied its own pick, every further second is
    spent producing an answer that can reach nobody and a record that says it did.
    """
    brain = AgentBrain(doorbell=SilentDoorbell(), timeout=None)
    view = world_view()
    view.decision_deadline_ms = 300

    # Driven from a watchdog thread rather than called directly, because the regression this
    # guards is *blocking forever* — and a direct call would express that as a hung suite with
    # no failing test in it, which is the same shape of unhelpful as the bug (§5.4: the run that
    # looks green is the one nobody investigates; the run that never finishes is the one nobody
    # can read).
    raised: list[BaseException] = []

    def call() -> None:
        try:
            brain.decide(view)
        except BaseException as exc:  # noqa: BLE001 — recorded and re-asserted below
            raised.append(exc)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "the brain waited past the deadline the engine gave it"
    assert raised and isinstance(raised[0], BrainError)
    assert "300ms deadline" in str(raised[0])


def test_the_tighter_of_the_two_deadlines_is_the_one_waited_on() -> None:
    """Two clocks, and neither is authoritative on its own.

    `NA_AGENT_TIMEOUT` says how long *we* will wait; the engine's deadline says how long the
    *game* will still listen. Taking the configured one when it is looser re-creates the bug;
    taking the engine's when it is looser abandons decisions nobody had given up on. Only the
    minimum is safe in both directions, which is why this asserts the arithmetic rather than
    just the fast path.
    """
    from neural_amplifier.agent_brain import ABANDON_MARGIN_SECONDS

    view = world_view()
    view.decision_deadline_ms = 2500

    # Engine tighter: shave the margin so the orchestrator loses the race deliberately.
    loose = AgentBrain(doorbell=SilentDoorbell(), timeout=600.0)
    timeout, reason = loose._deadline(view)
    assert timeout == pytest.approx(2.5 - ABANDON_MARGIN_SECONDS)
    assert reason is not None and "moved on" in reason

    # Configured tighter: it expires first, so the reason must not blame a deadline that was
    # never reached — a misleading cause is worse here than a generic one.
    tight = AgentBrain(doorbell=SilentDoorbell(), timeout=1.0)
    assert tight._deadline(view) == (1.0, None)


def test_a_very_tight_deadline_still_leaves_the_agent_a_chance_to_answer() -> None:
    """Subtracting a fixed margin goes negative below 250ms, and a negative wait abandons
    *instantly* — an adapter configured tight would offer decisions no agent could ever take,
    while the log blamed the agent for not answering. Shaving proportionally keeps the wait
    positive for any positive deadline.
    """
    brain = AgentBrain(doorbell=SilentDoorbell(), timeout=None)
    view = world_view()
    view.decision_deadline_ms = 100
    timeout, _ = brain._deadline(view)
    assert timeout == pytest.approx(0.05)


def test_no_engine_deadline_leaves_the_configured_wait_exactly_as_it_was() -> None:
    """Silence is not a bound. Every adapter that has not been upgraded says nothing here, and
    inventing a deadline for them would degrade decisions the game was still blocked on.
    """
    forever = AgentBrain(doorbell=SilentDoorbell(), timeout=None)
    assert forever._deadline(world_view()) == (None, None)
    configured = AgentBrain(doorbell=SilentDoorbell(), timeout=30.0)
    assert configured._deadline(world_view()) == (30.0, None)


def test_a_deadline_of_zero_means_indefinite_not_immediate() -> None:
    """The adapter writes 0 for "wait indefinitely" (na_http.cpp arms its socket deadline only
    for `timeout_ms > 0`), and `NA_AGENT_TIMEOUT` already reads 0 the same way. The opposite
    reading turns a deliberate no-limit run into an instant fallback on every decision, with
    nothing in the log that looks like a misconfiguration.
    """
    brain = AgentBrain(doorbell=SilentDoorbell(), timeout=45.0)
    view = world_view()
    view.decision_deadline_ms = 0
    assert brain._deadline(view) == (45.0, None)


# -------------------------------------------------------------------- doorbell


def test_doorbell_refuses_an_unsafe_decision_id() -> None:
    """The boundary check. Ids are generated and tame today; this is what keeps that true."""
    doorbell = Doorbell(target="does-not-exist")
    assert doorbell.ring("id with spaces; rm -rf ~") is False
    assert doorbell.ring("../../etc/passwd") is False


def test_doorbell_drops_an_unsafe_surface_rather_than_the_whole_ring() -> None:
    """A suspect surface id costs its own mention, not the notification."""
    doorbell = Doorbell(target="", enabled=False)
    assert doorbell.ring("base.production-1", "not; safe") is False  # disabled, but no raise


def test_doorbell_survives_a_dead_pane() -> None:
    """Best-effort by construction: the decision is already queued, so a failed nudge must
    never fail the turn."""
    doorbell = Doorbell(target="no-such-session:0.0")
    assert doorbell.ring("base.production-1") is False
