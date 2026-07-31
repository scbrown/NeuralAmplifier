"""Checking an order against the state it is about to be applied to.

The guard the agent-brain pivot made necessary. A model answered in seconds with the game
blocked, so the board could not move between the snapshot and the apply. An agent takes as long
as it likes and keeps a session across a whole game, so it can reason from a belief about a base
rather than from the world view in front of it — and the adapter replays one cached decision
across the engine's several calls per base-turn, by which point the board genuinely has moved.

What is testable is bounded by what is buildable: with no hot board graph (Hank role (d), not
built) this reasons only over numbers the world view already declares. Every check is arithmetic
on figures the adapter published, which is why a denial here is a fact rather than a guess.
"""

from __future__ import annotations

from neural_amplifier.contract import Action, Choice, Orders, Tradeoff, WorldView
from neural_amplifier.hank import CitationGuard, GuardChain, StateGuard
from neural_amplifier.knowledge import Ruling, apply


def view(
    actions: list[Action],
    metrics: dict[str, float] | None = None,
    tradeoffs: list[Tradeoff] | None = None,
) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=42,
        faction="Gaians",
        surface_id="base.hurry",
        action_space=actions,
        metrics=metrics,
        tradeoffs=tradeoffs,
    )


HURRY = Action(
    id="hurry:now",
    action="Hurry production",
    effects={"energy_reserves": -81.0, "minerals_remaining": -26.0},
)
WAIT = Action(id="hurry:none", action="Do not hurry")


def test_an_affordable_order_is_allowed() -> None:
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 82})
    ruling = StateGuard().rule(Orders(choices=[Choice(action_id="hurry:now")]), world_view)
    assert ruling.verdict == "allow"
    assert ruling.stripped == ()


def test_an_order_the_state_can_no_longer_pay_for_is_denied() -> None:
    """The case this exists for.

    The action space offered `hurry:now` when reserves were 82. By the time the order lands
    they are 40 — the agent spent them elsewhere, or reasoned from a stale memory of the board.
    Still legal by the engine's list; no longer payable.
    """
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 40})
    orders = Orders(choices=[Choice(action_id="hurry:now")])
    ruling = StateGuard().rule(orders, world_view)
    assert ruling.verdict == "deny"
    assert ruling.stripped == ("hurry:now",)
    assert "only 40" in ruling.advisories[0]
    # And the denial actually removes it, rather than merely commenting on it.
    assert apply(orders, ruling).choices == []


def test_spending_exactly_what_is_available_is_allowed() -> None:
    """Reserves to zero is a legitimate move, and an off-by-one here would forbid every
    all-in purchase the game permits."""
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 81})
    assert StateGuard().rule(
        Orders(choices=[Choice(action_id="hurry:now")]), world_view
    ).verdict == "allow"


def test_an_unreported_metric_is_uncheckable_not_violated() -> None:
    """Absent must never read as failing.

    The alternative is inventing a baseline and denying a legal move on the strength of it,
    which turns a gap in the adapter into a wrong answer in the game.
    """
    world_view = view([HURRY, WAIT], metrics={"labs_output": 6})
    assert StateGuard().rule(
        Orders(choices=[Choice(action_id="hurry:now")]), world_view
    ).verdict == "allow"


def test_no_metrics_at_all_allows() -> None:
    assert StateGuard().rule(
        Orders(choices=[Choice(action_id="hurry:now")]), view([HURRY, WAIT])
    ).verdict == "allow"


def test_an_action_with_no_declared_effects_is_not_second_guessed() -> None:
    """`effects` is a declaration, and its absence is not a claim of zero cost. Guessing a
    cost from elsewhere in the payload is exactly the engine knowledge the orchestrator must
    not hold (invariant 2)."""
    world_view = view([WAIT], metrics={"energy_reserves": 0})
    assert StateGuard().rule(
        Orders(choices=[Choice(action_id="hurry:none")]), world_view
    ).verdict == "allow"


def test_gains_are_never_denied() -> None:
    """A positive effect cannot make a metric unaffordable, and treating it symmetrically
    would deny orders that improve the position."""
    gain = Action(id="sell:x", action="Sell", effects={"energy_reserves": +50})
    world_view = view([gain], metrics={"energy_reserves": 0})
    assert StateGuard().rule(
        Orders(choices=[Choice(action_id="sell:x")]), world_view
    ).verdict == "allow"


def test_a_violated_directive_warns_but_never_denies() -> None:
    """Priorities exist so a decision can *outrank* a plan.

    Denying here would make directives absolute, which the design explicitly rejects — a
    standing plan losing to an urgent move is the mechanism working, not failing.
    """
    tradeoff = Tradeoff(
        action_id="hurry:now",
        directive_id="fund-weather-paradigm",
        metric="energy_reserves",
        delta=-81,
        projected=1,
        would_violate=True,
        directive_priority=7,
    )
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 82}, tradeoffs=[tradeoff])
    orders = Orders(choices=[Choice(action_id="hurry:now")])
    ruling = StateGuard().rule(orders, world_view)
    assert ruling.verdict == "allow"
    assert ruling.stripped == ()
    assert any("fund-weather-paradigm" in a and "priority 7" in a for a in ruling.advisories)
    assert apply(orders, ruling).choices, "a warning must not remove the choice"


def test_a_directive_on_an_unchosen_action_is_silent() -> None:
    """Only what was actually chosen is worth an advisory; the rest is noise on the record."""
    tradeoff = Tradeoff(
        action_id="hurry:now",
        directive_id="fund-weather-paradigm",
        metric="energy_reserves",
        delta=-81,
        projected=1,
        would_violate=True,
    )
    world_view = view([HURRY, WAIT], metrics={"energy_reserves": 82}, tradeoffs=[tradeoff])
    ruling = StateGuard().rule(Orders(choices=[Choice(action_id="hurry:none")]), world_view)
    assert ruling.advisories == ()


def test_an_action_outside_the_space_is_left_to_validate() -> None:
    """validate() strips these before the guard sees them. Reaching here means the two
    disagree, which is not this guard's bug to fix — nor its bug to hide."""
    world_view = view([WAIT], metrics={"energy_reserves": 0})
    assert StateGuard().rule(
        Orders(choices=[Choice(action_id="unit:999")]), world_view
    ).verdict == "allow"


# ------------------------------------------------------------------------ chain


class Denies:
    name = "denies"

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        return Ruling(verdict="deny", stripped=("hurry:now",), reason="nope")


class Allows:
    name = "allows"

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        return Ruling(verdict="allow", advisories=("fine by me",))


class Degraded:
    name = "degraded"

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        return Ruling(verdict="allow", degraded=True, reason="guard was down")


def test_deny_wins_regardless_of_order() -> None:
    """Otherwise registration order silently decides policy — a configuration bug that stays
    invisible until the day it matters."""
    orders = Orders(choices=[Choice(action_id="hurry:now")])
    world_view = view([HURRY])
    for chain in (GuardChain(Allows(), Denies()), GuardChain(Denies(), Allows())):
        ruling = chain.rule(orders, world_view)
        assert ruling.verdict == "deny"
        assert ruling.stripped == ("hurry:now",)


def test_the_chain_keeps_every_advisory() -> None:
    ruling = GuardChain(Allows(), StateGuard()).rule(
        Orders(choices=[Choice(action_id="hurry:now")]),
        view([HURRY], metrics={"energy_reserves": 10}),
    )
    assert "fine by me" in ruling.advisories
    assert any("only 10" in a for a in ruling.advisories)


def test_degradation_propagates_through_the_chain() -> None:
    """A record reporting a clean pass from a guard that never ran is worse than one
    reporting nothing."""
    ruling = GuardChain(StateGuard(), Degraded()).rule(
        Orders(choices=[Choice(action_id="hurry:none")]), view([WAIT])
    )
    assert ruling.degraded is True
    assert ruling.reason and "down" in ruling.reason


def test_an_empty_chain_allows() -> None:
    """A guard that is down allows, and so does one that was never wired."""
    assert GuardChain().rule(Orders(), view([WAIT])).verdict == "allow"


def test_the_chain_composes_the_two_real_guards() -> None:
    """The shipped configuration: state preconditions plus citation integrity."""
    chain = GuardChain(StateGuard(), CitationGuard())
    ruling = chain.rule(
        Orders(choices=[Choice(action_id="hurry:now")], cited=["fac:invented"]),
        view([HURRY], metrics={"energy_reserves": 5}),
    )
    assert ruling.verdict == "deny"
    assert any("only 5" in a for a in ruling.advisories)
