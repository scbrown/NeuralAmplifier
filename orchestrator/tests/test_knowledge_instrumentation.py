"""Instrumentation on the knowledge seam.

These tests exist because the interesting failures here are *silent*. A knowledge layer
that stops being wired, or one that retrieves diligently and is then ignored by the model,
both produce records that look fine. What follows pins the distinctions that make those
visible.
"""

from __future__ import annotations

from neural_amplifier.contract import Orders
from neural_amplifier.knowledge import Grounding, Ruling, summarise


def test_absent_is_not_the_same_as_empty_or_degraded() -> None:
    """Three ways to have no facts, and a dashboard must tell them apart.

    Collapsing them is how a retriever gets unwired and nobody notices: "0 hits" reads
    as a quiet day rather than a broken deployment.
    """
    absent = summarise(Grounding(reason="no retriever configured"), Ruling(), guarded=False)
    assert absent.quipu_absent and not absent.quipu_degraded and absent.quipu_hits == 0

    empty = summarise(Grounding(), Ruling(), guarded=False)
    assert not empty.quipu_absent and not empty.quipu_degraded and empty.quipu_hits == 0

    broken = summarise(Grounding(degraded=True, reason="boom"), Ruling(), guarded=False)
    assert broken.quipu_degraded and not broken.quipu_absent


def test_guard_absent_is_distinguished_from_guard_allowing() -> None:
    """A guard that is down allows; a guard never wired also allows.

    Only one of those is a deployment problem, so the record has to say which.
    """
    unwired = summarise(Grounding(), Ruling(verdict="allow"), guarded=False)
    assert unwired.hank_absent and unwired.hank_verdict is None

    allowed = summarise(Grounding(), Ruling(verdict="allow"), guarded=True)
    assert not allowed.hank_absent and allowed.hank_verdict == "allow"


def test_utilisation_measures_what_was_read_not_what_was_offered() -> None:
    grounding = Grounding(
        facts=("Colony Pod founds a new base", "Formers terraform", "Scout Patrol scouts"),
        fact_ids=("f1", "f2", "f3"),
    )
    used = summarise(grounding, Ruling(), guarded=False, cited=["f2"])
    assert used.quipu_hits == 3
    assert used.quipu_cited == ["f2"]
    assert used.utilisation == 1 / 3


def test_ignored_grounding_is_visible() -> None:
    """The case this instrumentation exists for: retrieval worked, nothing was read."""
    grounding = Grounding(facts=("a", "b"), fact_ids=("f1", "f2"))
    ignored = summarise(grounding, Ruling(), guarded=False, cited=[])
    assert ignored.quipu_hits == 2
    assert ignored.utilisation == 0.0


def test_unmeasurable_utilisation_is_none_not_zero() -> None:
    """Zero would read as "the model ignored the facts". Unlabelled facts are unknowable."""
    unlabelled = summarise(Grounding(facts=("a", "b")), Ruling(), guarded=False, cited=["x"])
    assert unlabelled.quipu_hits == 2
    assert unlabelled.utilisation is None


def test_hallucinated_citation_cannot_inflate_utilisation() -> None:
    """A model naming a fact it was never given must not land in the provenance block.

    Otherwise the record asserts that something informed the decision when nothing did —
    which is worse than no instrumentation, because it is confidently wrong.
    """
    grounding = Grounding(facts=("a",), fact_ids=("f1",))
    out = summarise(grounding, Ruling(), guarded=False, cited=["f1", "f9", "invented"])
    assert out.quipu_cited == ["f1"]
    assert out.utilisation == 1.0


def test_duplicate_citations_counted_once() -> None:
    grounding = Grounding(facts=("a", "b"), fact_ids=("f1", "f2"))
    out = summarise(grounding, Ruling(), guarded=False, cited=["f1", "f1", "f1"])
    assert out.quipu_cited == ["f1"]
    assert out.utilisation == 0.5


def test_orders_carry_citations() -> None:
    assert Orders().cited == []
    assert Orders(cited=["f1", "f2"]).cited == ["f1", "f2"]


def test_latency_stamping_does_not_drop_fields() -> None:
    """Regression: ``_with_latency`` used to rebuild Grounding field by field.

    It silently lost ``fact_ids`` the moment that field was added — retrieval returned ids,
    the record showed none, and utilisation read as unmeasurable on every single decision.
    The bug is invisible in isolation and only shows up end to end, which is exactly why it
    survived. ``dataclasses.replace`` cannot forget a field.
    """
    from neural_amplifier.knowledge import _with_latency

    g = Grounding(facts=("a", "b"), fact_ids=("f1", "f2"), degraded=False, reason="why")
    stamped = _with_latency(g, 42)
    assert stamped.latency_ms == 42
    assert stamped.fact_ids == ("f1", "f2")
    assert stamped.facts == ("a", "b")
    assert stamped.reason == "why"


def test_validation_preserves_citations() -> None:
    """Regression: validation rebuilt Orders from three fields and dropped ``cited``.

    Same shape of bug as above, and the visible symptom was the guard reporting "grounding
    unread" on decisions that had cited correctly. Validation rules on ACTIONS; a citation is
    not an action and is not this layer's to judge.
    """
    from neural_amplifier.contract import Action, Choice, WorldView
    from neural_amplifier.validate import validate

    wv = WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="Gaians",
        action_space=[Action(id="unit:1", action="Formers")],
    )
    orders = Orders(choices=[Choice(action_id="unit:1")], cited=["unit:formers"])
    assert validate(orders, wv).orders().cited == ["unit:formers"]
