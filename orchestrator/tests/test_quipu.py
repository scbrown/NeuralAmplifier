"""Quipu-backed retrieval.

Split deliberately: the query construction and row formatting are pure and run
everywhere, and a handful of integration tests skip unless a ``quipu-server``
is actually reachable.

The batched query needs **quipu >= 0.3.13**, where ``VALUES`` landed (quipu #51).
Verified against 0.3.23 by loading ``datalinks/thinker/alphax.ttl`` into a real
store and diffing the new query's rows against the old ``||`` disjunction's:
identical, so the rewrite is a no-op in meaning and a saving in query size.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error

import pytest

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Action, WorldView
from neural_amplifier.datalinks.quipu import (
    NAMESPACE,
    QuipuRetriever,
    build_query,
    build_turn_query,
    escape,
    format_row,
)
from neural_amplifier.orchestrator import Orchestrator

QUIPU_URL = os.environ.get("NA_QUIPU_URL", "")


def asked_labels(query: str) -> list[str]:
    """The labels one built query binds — parsed out of the `VALUES ?label { … }` block.

    A helper rather than a substring count because the shape is now a pattern rather than a
    FILTER: counting `?label = ` occurrences was the old query's accident, and a test that
    asserts on the accident stops testing the thing anyone cares about, which is *which options
    got asked about*.
    """
    block = re.search(r"VALUES \?label \{(.*?)\}", query, re.S)
    assert block, query
    return re.findall(r'"((?:[^"\\]|\\.)*)"', block.group(1))


def view(*actions: str, engine: str = "thinker", faction_id: int | None = None) -> WorldView:
    return WorldView(
        engine=engine,
        scope="base",
        turn=1,
        faction="GAIANS",
        faction_id=faction_id,
        surface_id="base.production",
        action_space=[Action(id=f"a{i}", action=a) for i, a in enumerate(actions)],
    )


# --- query construction ----------------------------------------------------


def test_the_engine_filter_is_never_omitted() -> None:
    """Emission tags appliesToEngine; if retrieval does not constrain on it the
    tag is decoration and a Thinker house-rule surfaces in a GLSMAC game. This
    is the half of the guardrail that protects an actual decision."""
    query = build_query(["Recycling Tanks"], "glsmac")
    assert 'VALUES ?eng { "smac" "glsmac" }' in query
    assert "thinker" not in query


def test_stock_smac_needs_no_second_engine_binding() -> None:
    """smac facts are legitimate everywhere, so the relation collapses to one row."""
    assert 'VALUES ?eng { "smac" }' in build_query(["Recycling Tanks"], "smac")


def test_the_query_uses_values_as_the_architecture_specifies() -> None:
    """It used to build a `||` chain: quipu 0.3.11 rejected both VALUES and FILTER(?x IN (…))
    with 'unsupported graph pattern' / 'unsupported FILTER expression'. Both landed upstream in
    0.3.13 (quipu #51, #52), so the workaround is gone and this is what
    knowledge-architecture.md specified all along.

    Not only tidier: the disjunction emitted one comparison per label per variable, so the
    FILTER grew linearly with the turn's action space — on the exact path build_query exists to
    keep bounded.
    """
    query = build_query(["Recycling Tanks", "Energy Bank"], "thinker")
    assert 'VALUES ?label { "Recycling Tanks" "Energy Bank" }' in query
    assert "||" not in query
    assert "FILTER" not in query


def test_the_two_bindings_stay_separate_blocks() -> None:
    """Label and engine are a cross product — every offered label, in either legitimate plane.
    One block over both variables would have to enumerate the pairs row by row, reintroducing
    the linear growth this replaced."""
    query = build_query(["Recycling Tanks", "Energy Bank"], "thinker")
    assert query.count("VALUES") == 2
    assert "VALUES (" not in query  # the multi-variable form, which this deliberately is not


def test_a_configured_dataset_is_sent_on_every_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """The NA graph must constrain the request, not merely exist beside ROOT."""
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    seen: dict[str, object] = {}

    class Response:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"rows": []}'

    def open_request(request, *, timeout):  # type: ignore[no-untyped-def]
        del timeout
        seen.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(retriever._opener, "open", open_request)
    assert retriever.query("SELECT * WHERE { ?s ?p ?o }") == []
    assert seen["graph"] == "urn:na:dataset"


def test_turn_grounding_queries_once_then_filters_decisions_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    calls: list[str] = []
    rows = [
        {"f": f"{NAMESPACE}facility/recycling", "label": "Recycling Tanks", "tier": "canonical"},
        {"f": f"{NAMESPACE}facility/energy", "label": "Energy Bank", "tier": "canonical"},
    ]

    def query(sparql: str) -> list[dict[str, object]]:
        calls.append(sparql)
        return rows

    monkeypatch.setattr(retriever, "query", query)
    assert retriever.prime_turn(42, 1) == 2
    first = retriever.retrieve(
        view("Recycling Tanks", faction_id=1).model_copy(update={"turn": 42})
    )
    second = retriever.retrieve(view("Energy Bank", faction_id=1).model_copy(update={"turn": 42}))
    assert first.hits == second.hits == 1
    assert len(calls) == 1
    assert "VALUES ?label" not in calls[0]


def test_turn_grounding_cache_is_faction_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    monkeypatch.setattr(retriever, "query", lambda _sparql: [])
    retriever.prime_turn(42, 1)

    with pytest.raises(RuntimeError, match="not primed"):
        retriever.retrieve(view("Recycling Tanks", faction_id=2).model_copy(update={"turn": 42}))


def test_turn_query_keeps_the_engine_tenancy_guard() -> None:
    query = build_turn_query("thinker")
    assert 'VALUES ?eng { "smac" "thinker" }' in query
    assert "VALUES ?label" not in query


def test_optional_maintenance_does_not_drop_rows() -> None:
    """A facility with no maintenance triple must still come back; an inner
    pattern would silently filter it out."""
    assert "OPTIONAL" in build_query(["Headquarters"], "thinker")


def test_literals_are_escaped() -> None:
    """Action names come from the adapter. An unescaped quote is a parse error
    at best and a query rewritten by the game's data at worst."""
    assert escape('Sky "Hydroponics" Lab') == 'Sky \\"Hydroponics\\" Lab'
    assert escape("back\\slash") == "back\\\\slash"
    assert '\\"' in build_query(['A "quoted" name'], "thinker")


# --- row formatting --------------------------------------------------------


def test_a_non_canonical_tier_is_shown_to_the_model() -> None:
    """Presenting a house-rule as canonical is the failure the whole plane
    guards against, so the tier rides into the prompt."""
    row = {"label": "Recycling Tanks", "cost": 4, "effect": "Bonus Resources", "tier": "house-rule"}
    assert format_row(row).endswith("[house-rule]")


def test_canonical_tier_is_not_annotated_but_the_source_always_is() -> None:
    """Two different jobs for the bracket, and only one is suppressible.

    Tagging every canonical fact with its tier is noise on the common case, and noise is
    what stops people reading the tag that matters. The source is different: a fact whose
    origin cannot be named is not auditable, and every fact is required to carry at least
    one pointer back into datalinks. So canonical facts lose the tier and keep the source.
    """
    row = {"label": "Recycling Tanks", "effect": "Bonus Resources", "tier": "canonical"}
    assert "canonical" not in format_row(row)
    assert "[" not in format_row(row)  # no tier, and no source supplied here

    sourced = {**row, "src": f"{NAMESPACE}source/alphax-txt"}
    out = format_row(sourced)
    assert "canonical" not in out
    assert "[src:alphax-txt]" in out


def test_zero_maintenance_is_omitted() -> None:
    row = {"label": "Headquarters", "cost": 5, "maint": 0, "effect": "Efficiency"}
    assert "upkeep" not in format_row(row)
    assert "upkeep 2/turn" in format_row({**row, "maint": 2})


# --- retrieval behaviour (no server needed) --------------------------------


class FakeQuipu(QuipuRetriever):
    def __init__(self, rows: list[dict[str, object]], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.rows = rows
        self.queries: list[str] = []

    def query(self, sparql: str) -> list[dict[str, object]]:  # type: ignore[override]
        self.queries.append(sparql)
        return self.rows


@pytest.fixture
def fake_rows() -> list[dict[str, object]]:
    """Three options the store knows about, so a dropped one is a BOUND and not a gap."""
    return [
        {"label": "A", "effect": "alpha"},
        {"label": "B", "effect": "beta"},
        {"label": "C", "effect": "gamma"},
    ]


def test_retrieval_is_bounded_to_the_action_space() -> None:
    """Budget discipline: the prompt grows with the choices on offer, not with
    the rulebook."""
    retriever = FakeQuipu([])
    retriever.retrieve(view("Recycling Tanks", "Energy Bank"))
    query = retriever.queries[0]
    assert len(asked_labels(query)) == 2


def test_one_batched_query_per_decision() -> None:
    """Not one call per action — that is the difference between a bounded
    per-turn cost and a linear one."""
    retriever = FakeQuipu([])
    retriever.retrieve(view("A", "B", "C", "D"))
    assert len(retriever.queries) == 1


def test_the_limit_caps_the_result_not_the_query(fake_rows: list[dict[str, object]]) -> None:
    """na-dhs. This test asserted the OPPOSITE, and carried no reason for it.

    Capping the candidate labels looks like the same saving and is not, because an action
    space is ORDERED BY CATEGORY: the engine lists units before facilities, so a cap of 12 on
    a 48-option `base.production` decision asked about seven units and not one facility — on
    a decision that is about facilities. Measured against the real store, the cap cost 13 of
    the 20 available facts and bought nothing: 12, 24 and 48 labels all returned in ~1151 ms.

    So the query asks about everything and the limit falls on what came back.
    """
    retriever = FakeQuipu(fake_rows, limit=2)
    grounding = retriever.retrieve(view("A", "B", "C"))
    assert len(asked_labels(retriever.queries[0])) == 3, "every option must be asked about"
    assert grounding.hits == 2, "the limit still bounds what reaches the prompt"


def test_what_the_limit_drops_is_recorded_not_discarded(
    fake_rows: list[dict[str, object]],
) -> None:
    """`budget.py` promises "nothing is dropped silently"; the count cap broke that promise.

    It dropped upstream of the budget layer, so nothing could report it and a decision record
    could not tell a truncated grounding from a complete one.
    """
    grounding = FakeQuipu(fake_rows, limit=2).retrieve(view("A", "B", "C"))
    assert grounding.shed == ("C",)
    assert grounding.ungrounded == 1


def test_a_shed_option_and_an_unknown_option_are_different_findings() -> None:
    """Opposite remedies: one bound is ours to raise, the other is a gap in the graph.

    Collapsing them hides a self-inflicted truncation inside what reads as an incomplete
    rulebook — and an option nobody has a rule for is not a bug at all.
    """
    rows: list[dict[str, object]] = [{"label": "A", "effect": "x"}, {"label": "B", "effect": "y"}]
    grounding = FakeQuipu(rows, limit=1).retrieve(view("A", "B", "C"))
    assert grounding.shed == ("B",), "had a fact, dropped by our bound"
    assert grounding.unmatched == ("C",), "the store has no rule for it"


def test_facts_keep_action_space_order() -> None:
    """The store returns rows in its own order; the prompt should read in the
    order the engine offered the choices."""
    retriever = FakeQuipu(
        [
            {"label": "Energy Bank", "cost": 8, "effect": "Economy Bonus"},
            {"label": "Recycling Tanks", "cost": 4, "effect": "Bonus Resources"},
        ]
    )
    facts = retriever.retrieve(view("Recycling Tanks", "Energy Bank")).facts
    assert facts[0].startswith("Recycling Tanks")
    assert facts[1].startswith("Energy Bank")


def test_an_empty_action_space_does_not_query_at_all() -> None:
    retriever = FakeQuipu([])
    assert retriever.retrieve(view()).hits == 0
    assert retriever.queries == []


def test_duplicate_actions_are_asked_for_once() -> None:
    retriever = FakeQuipu([])
    retriever.retrieve(view("Recycling Tanks", "Recycling Tanks"))
    assert len(asked_labels(retriever.queries[0])) == 1


# --- subjects: surfaces that decide ABOUT an entity ------------------------


def _hurry_view(subjects: list[str] | None) -> WorldView:
    """A ``base.hurry`` world view: two options, neither of them a datalinks entity."""
    return WorldView(
        engine="thinker",
        scope="base",
        turn=35,
        faction="University",
        surface_id="base.hurry",
        action_space=[
            Action(id="hurry:none", action="Do not hurry"),
            Action(id="hurry:now", action="Hurry production"),
        ],
        subjects=subjects,
    )


def test_a_surface_whose_actions_are_not_entities_retrieves_via_its_subject() -> None:
    """The ``base.hurry`` regression.

    "Hurry production" and "Do not hurry" are verbs, not things — neither resolves in any
    datalinks, so keying retrieval purely off action labels made the entire surface ungrounded.
    Measured consequence: 0.60 stability, the least stable surface we have, decided with zero
    facts in the prompt.
    """
    retriever = FakeQuipu([])
    retriever.retrieve(_hurry_view(["Colony Pod"]))
    # The subject is asked about alongside the verbs, and first — `retrieve` puts subjects ahead
    # of action labels so the budget sheds the verbs before the thing the decision is about.
    asked = asked_labels(retriever.queries[0])
    assert asked[0] == "Colony Pod"
    assert "Hurry production" in asked


def test_the_subject_survives_a_limit_that_drops_everything_else() -> None:
    """Order matters under a limit or a token budget.

    On a surface that names a subject, every option is about that one entity, so the subject is
    the least droppable fact in the payload — not the most.

    That intent is unchanged; only where the limit bites moved (na-dhs). It used to be checked
    by counting labels in the QUERY, which could only ever confirm that the subject was asked
    about first. Now it is checked on the GROUNDING, which is the thing the claim is actually
    about — the subject is what survives.
    """
    rows: list[dict[str, object]] = [
        {"label": "Colony Pod", "effect": "founds a new base"},
        {"label": "Hurry production", "effect": "spend energy"},
    ]
    grounding = FakeQuipu(rows, limit=1).retrieve(_hurry_view(["Colony Pod"]))
    assert grounding.facts[0].startswith("Colony Pod")
    assert "Hurry production" in grounding.shed


def test_no_subject_leaves_retrieval_exactly_as_it_was() -> None:
    """Additive by construction: every surface that predates ``subjects`` is untouched."""
    with_field = FakeQuipu([])
    with_field.retrieve(view("Recycling Tanks", "Energy Bank"))
    unset = FakeQuipu([])
    unset.retrieve(_hurry_view(None))

    assert len(asked_labels(with_field.queries[0])) == 2
    assert len(asked_labels(unset.queries[0])) == 2  # the two action labels, as before


def test_a_subject_that_repeats_an_action_label_is_asked_for_once() -> None:
    """``base.production`` could legitimately name the item it is already offering."""
    retriever = FakeQuipu([])
    retriever.retrieve(
        WorldView(
            engine="thinker",
            scope="base",
            turn=1,
            faction="GAIANS",
            surface_id="base.production",
            action_space=[Action(id="a0", action="Colony Pod")],
            subjects=["Colony Pod"],
        )
    )
    assert len(asked_labels(retriever.queries[0])) == 1


def test_a_rejected_query_raises_rather_than_returning_nothing() -> None:
    """The seam turns this into a degraded decision. Swallowing it here would
    make a broken query indistinguishable from a turn with no known rules."""

    class Rejecting(QuipuRetriever):
        def query(self, sparql: str) -> list[dict[str, object]]:  # type: ignore[override]
            raise RuntimeError("quipu rejected the query: unsupported graph pattern")

    with pytest.raises(RuntimeError, match="unsupported graph pattern"):
        Rejecting().retrieve(view("Recycling Tanks"))


def test_a_dead_quipu_degrades_the_decision_but_not_the_turn() -> None:
    """End to end through the seam: an unreachable store costs grounding, not
    the game."""

    class Down(QuipuRetriever):
        def query(self, sparql: str) -> list[dict[str, object]]:  # type: ignore[override]
            raise urllib.error.URLError("connection refused")

    result = Orchestrator(ScriptedBrain(), retriever=Down()).decide(
        view("Recycling Tanks").model_copy(
            update={
                "action_space": [
                    Action(id="a1", action="Recycling Tanks"),
                    Action(id="a2", action="end_turn"),
                ]
            }
        )
    )

    assert result.orders.choices
    assert result.record.degraded is False
    assert result.record.knowledge.quipu_degraded is True


# --- integration (needs a running quipu-server) ---------------------------

integration = pytest.mark.skipif(
    not QUIPU_URL, reason="set NA_QUIPU_URL to a running quipu-server to run these"
)


@integration
def test_live_retrieval_returns_grounded_facts() -> None:
    retriever = QuipuRetriever(QUIPU_URL, engine="thinker")
    grounding = retriever.retrieve(view("Recycling Tanks", "end_turn"))

    assert grounding.hits == 1
    assert "Recycling Tanks" in grounding.facts[0]
    assert grounding.degraded is False


@integration
def test_live_engine_filter_excludes_other_engines() -> None:
    """The committed graph is tagged engine=thinker, so a glsmac game must not
    see it — the anti-masquerade guardrail, measured rather than asserted."""
    assert QuipuRetriever(QUIPU_URL, engine="glsmac").retrieve(view("Recycling Tanks")).hits == 0
    assert QuipuRetriever(QUIPU_URL, engine="thinker").retrieve(view("Recycling Tanks")).hits == 1


@integration
def test_live_query_is_valid_sparql_for_quipu() -> None:
    """Guards the constructs Quipu does not implement: if a future change
    reintroduces VALUES or FILTER IN, this fails with quipu's own error."""
    rows = QuipuRetriever(QUIPU_URL).query(build_query(["Recycling Tanks"], "thinker"))
    assert isinstance(rows, list)


@integration
def test_live_decision_records_the_hits() -> None:
    result = Orchestrator(
        ScriptedBrain(), retriever=QuipuRetriever(QUIPU_URL, engine="thinker")
    ).decide(view("Recycling Tanks", "end_turn"))

    assert result.record.knowledge.quipu_hits == 1
    assert result.record.knowledge.quipu_degraded is False
    assert json.loads(result.record.model_dump_json())["knowledge"]["quipu_hits"] == 1


# --- budgeting -------------------------------------------------------------


def test_the_retriever_applies_the_token_budget() -> None:
    """Bounding the fact count says nothing about prompt size when one fact is
    a paragraph, so the retriever takes a token ceiling too."""
    rows = [{"label": n, "cost": 4, "effect": "e" * 200} for n in ("A", "B", "C")]
    grounding = FakeQuipu(rows, token_budget=30).retrieve(view("A", "B", "C"))

    assert grounding.hits < 4
    assert any("omitted for token budget" in f for f in grounding.facts)


def test_no_budget_means_no_truncation_note() -> None:
    """Default is unbounded — the action-space cap already bounds the common
    case, and an accounting line nobody needs is wasted prompt."""
    rows = [{"label": "A", "cost": 4, "effect": "e" * 500}]
    grounding = FakeQuipu(rows).retrieve(view("A"))

    assert grounding.hits == 1
    assert not any("omitted" in f for f in grounding.facts)
