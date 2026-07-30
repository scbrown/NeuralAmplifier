"""The citation-integrity policy — ``hank.py``.

Grounding is unfalsifiable without this: a decision that cites nothing and one that cites a
fact nobody supplied both look like reasoning from the outside.
"""

from __future__ import annotations

from neural_amplifier.contract import Orders, WorldView
from neural_amplifier.hank import CitationGuard, quipu_resolver


def view(*grounding: str) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=35,
        faction="Gaians",
        grounding=list(grounding),
    )


def test_clean_citation_allows() -> None:
    guard = CitationGuard()
    ruling = guard.rule(Orders(cited=["unit:formers"]), view("unit:formers Formers; terraforms"))
    assert ruling.verdict == "allow"
    assert ruling.advisories == ()


def test_fabricated_citation_is_flagged_but_never_denied() -> None:
    """A suspect justification does not make an order illegal.

    Denying a legal move because its reasoning was sloppy would break the game to make a point
    about bookkeeping — and legality is the engine's job, not the guard's.
    """
    guard = CitationGuard()
    ruling = guard.rule(Orders(cited=["unit:invented"]), view("unit:formers Formers"))
    assert ruling.verdict == "warn"
    assert "never offered" in ruling.advisories[0]
    assert ruling.stripped == ()


def test_offered_but_unresolvable_is_a_datalinks_problem_not_a_model_one() -> None:
    """Distinct advisory, because the fix is different: retrieval emitted a dead pointer."""
    guard = CitationGuard(resolver=quipu_resolver(None, known={"unit:formers"}))
    ruling = guard.rule(
        Orders(cited=["fac:ghost"]),
        view("unit:formers Formers", "fac:ghost Ghost Facility"),
    )
    assert ruling.verdict == "warn"
    assert any("do not resolve" in a for a in ruling.advisories)


def test_unread_grounding_is_surfaced_without_being_an_error() -> None:
    """A decision may legitimately turn on state rather than rules.

    But a run where this is always true is paying for retrieval nobody reads, which is exactly
    what the utilisation instrumentation exists to expose.
    """
    guard = CitationGuard()
    ruling = guard.rule(
        Orders(cited=[]), view("unit:formers Formers", "unit:colony-pod Colony Pod")
    )
    assert ruling.verdict == "warn"
    assert "none cited" in ruling.advisories[0]


def test_no_grounding_and_no_citations_is_silent() -> None:
    """Nothing was offered, nothing was claimed. There is nothing to report."""
    assert CitationGuard().rule(Orders(), view()).verdict == "allow"


def test_absent_resolver_does_not_manufacture_findings() -> None:
    """Cannot check existence is not the same as does not exist."""
    guard = CitationGuard(resolver=None)
    ruling = guard.rule(Orders(cited=["unit:formers"]), view("unit:formers Formers"))
    assert ruling.verdict == "allow"


def test_throwing_resolver_does_not_become_a_violation() -> None:
    """A store that blinks must not invent a policy breach."""

    def boom(_: str) -> bool:
        raise RuntimeError("store down")

    guard = CitationGuard(resolver=boom)
    ruling = guard.rule(Orders(cited=["unit:formers"]), view("unit:formers Formers"))
    assert ruling.verdict == "allow"


def test_duplicate_citations_reported_once() -> None:
    guard = CitationGuard()
    ruling = guard.rule(Orders(cited=["x", "x"]), view("unit:formers Formers"))
    assert len([a for a in ruling.advisories if "never offered" in a]) == 1
