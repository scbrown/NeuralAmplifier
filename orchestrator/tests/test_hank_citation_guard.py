"""The citation-integrity policy — ``hank.py``.

Grounding is unfalsifiable without this: a decision that cites nothing and one that cites a
fact nobody supplied both look like reasoning from the outside.
"""

from __future__ import annotations

from neural_amplifier.contract import Directive, DirectiveStatus, Orders, WorldView
from neural_amplifier.hank import CitationGuard, quipu_resolver


def view(*grounding: str, directives: list[DirectiveStatus] | None = None) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=35,
        faction="Gaians",
        grounding=list(grounding),
        directives=directives,
    )


def plan(*entities: str) -> list[DirectiveStatus]:
    """One standing directive naming datalinks entities — the other way an id reaches the brain."""
    return [
        DirectiveStatus(
            directive=Directive(
                id="fund-weather-paradigm",
                intent="Bank energy for the Weather Paradigm.",
                metric="energy_reserves",
                comparator="at_least",
                target=300.0,
                entities=list(entities),
            )
        )
    ]


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


# --- the other id space: entities a directive showed (na-zgz) ---------------


def test_an_entity_a_directive_showed_is_offered_not_fabricated() -> None:
    """The bug this suite shipped without catching.

    A directive's ``entities`` are grounding fact ids — that shared id space is exactly what
    makes the multi-hop walk work — but they arrive through the directives block. Reading the
    offered set out of grounding alone flagged three to four runs in five with
    ``never offered: fac:the-weather-paradigm`` for an id the world view had genuinely shown.
    """
    guard = CitationGuard()
    ruling = guard.rule(
        Orders(cited=["fac:the-weather-paradigm"]),
        view("unit:formers Formers", directives=plan("fac:the-weather-paradigm")),
    )
    assert ruling.verdict == "allow"
    assert ruling.advisories == ()


def test_a_genuinely_invented_id_is_still_caught_when_directives_are_present() -> None:
    """Widening the offered set must not turn the check off — the whole point is that a
    fabricated citation stays visible."""
    guard = CitationGuard()
    ruling = guard.rule(
        Orders(cited=["fac:the-weather-paradigm", "unit:invented"]),
        view("unit:formers Formers", directives=plan("fac:the-weather-paradigm")),
    )
    assert ruling.verdict == "warn"
    unoffered = [a for a in ruling.advisories if "never offered" in a]
    assert len(unoffered) == 1
    assert "unit:invented" in unoffered[0]
    assert "fac:the-weather-paradigm" not in unoffered[0]


def test_unread_grounding_stays_a_statement_about_retrieval() -> None:
    """Directive entities are not retrieved, so they must not make an ungrounded decision look
    like one that ignored retrieval — nothing was paid for and nothing went unread."""
    guard = CitationGuard()
    ruling = guard.rule(Orders(), view(directives=plan("fac:the-weather-paradigm")))
    assert ruling.verdict == "allow"


def test_a_directive_entity_that_does_not_resolve_is_a_graph_problem() -> None:
    """Now that these ids count as offered they are held to the same graph check. A directive
    naming a node that does not exist is a dead pointer exactly as a bad retrieval hit is."""
    guard = CitationGuard(resolver=quipu_resolver(None, known={"unit:formers"}))
    ruling = guard.rule(
        Orders(cited=["fac:ghost"]),
        view("unit:formers Formers", directives=plan("fac:ghost")),
    )
    assert ruling.verdict == "warn"
    assert any("do not resolve" in a for a in ruling.advisories)
