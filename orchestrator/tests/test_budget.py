"""Token budget discipline — docs/quipu-integration.md (K2)."""

from __future__ import annotations

from neural_amplifier.datalinks import Fact, apply_budget


def rule(text: str) -> Fact:
    return Fact(text, kind="rule")


def tactic(text: str) -> Fact:
    return Fact(text, kind="tactic")


def test_tactics_are_shed_before_rules() -> None:
    """Rules are correctness — a canonical cost the model must not get wrong.
    Tactics are optimization. Shedding the optimization first is also the
    retrieval precedence, so the budget cannot invert the trust ordering to
    save a few tokens."""
    facts = [tactic("t" * 70), rule("r" * 70)]
    result = apply_budget(facts, limit=20)

    assert [f.kind for f in result.kept] == ["rule"]
    assert [f.kind for f in result.dropped] == ["tactic"]


def test_a_long_tactic_cannot_crowd_out_a_later_rule() -> None:
    """A single greedy pass in list order would spend the budget on the tactic
    at the front and drop the rule behind it — inverting precedence by
    accident rather than by decision."""
    result = apply_budget([tactic("t" * 200), rule("short rule")], limit=10)

    assert [f.kind for f in result.kept] == ["rule"]


def test_order_is_preserved_within_a_class() -> None:
    """The retriever already ranked by action space; reordering here would
    change which choice the model sees explained first."""
    facts = [rule("alpha"), rule("beta"), rule("gamma")]
    assert [f.text for f in apply_budget(facts, limit=100).kept] == ["alpha", "beta", "gamma"]


def test_everything_fits_when_the_budget_is_ample() -> None:
    result = apply_budget([rule("a"), tactic("b")], limit=1000)
    assert result.within_budget is True
    assert len(result.kept) == 2


def test_a_zero_budget_drops_everything_rather_than_ignoring_the_limit() -> None:
    """Failing open here would make the budget advisory."""
    result = apply_budget([rule("a")], limit=0)
    assert result.kept == []
    assert len(result.dropped) == 1


def test_a_shorter_fact_still_fits_after_an_oversized_one() -> None:
    """Stopping at the first fact that doesn't fit would drop facts the budget
    could actually afford."""
    result = apply_budget([rule("r" * 400), rule("tiny")], limit=15)
    assert [f.text for f in result.kept] == ["tiny"]


def test_nothing_is_dropped_silently() -> None:
    """A truncated prompt that reads as complete is the same failure shape as
    the all-fallback run: the decision looks informed and is not."""
    result = apply_budget([rule("r" * 100), tactic("t" * 100), tactic("u" * 100)], limit=5)
    facts = result.grounding().facts

    assert any("omitted for token budget" in f for f in facts)
    assert "2 tactics" in facts[-1]


def test_a_run_within_budget_adds_no_note() -> None:
    """Don't spend prompt on an accounting line when nothing was shed."""
    facts = apply_budget([rule("a")], limit=1000).grounding().facts
    assert not any("omitted" in f for f in facts)


def test_dropped_counts_are_reportable_by_kind() -> None:
    result = apply_budget([rule("r" * 100), tactic("t" * 100)], limit=1)
    assert result.dropped_by_kind() == {"rule": 1, "tactic": 1}


def test_cost_is_never_zero() -> None:
    """A fact that rounds to zero tokens would let an unbounded number
    through."""
    assert Fact("").cost >= 1
    assert Fact("a").cost >= 1
