"""Quipu-backed retrieval.

Split deliberately: the query construction and row formatting are pure and run
everywhere, and a handful of integration tests skip unless a ``quipu-server``
is actually reachable. Verified against quipu 0.3.11 during development.
"""

from __future__ import annotations

import json
import os
import urllib.error

import pytest

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Action, WorldView
from neural_amplifier.datalinks.quipu import (
    QuipuRetriever,
    build_query,
    escape,
    format_row,
)
from neural_amplifier.orchestrator import Orchestrator

QUIPU_URL = os.environ.get("NA_QUIPU_URL", "")


def view(*actions: str, engine: str = "thinker") -> WorldView:
    return WorldView(
        engine=engine,
        scope="base",
        turn=1,
        faction="GAIANS",
        surface_id="base.production",
        action_space=[Action(id=f"a{i}", action=a) for i, a in enumerate(actions)],
    )


# --- query construction ----------------------------------------------------


def test_the_engine_filter_is_never_omitted() -> None:
    """Emission tags appliesToEngine; if retrieval does not filter on it the
    tag is decoration and a Thinker house-rule surfaces in a GLSMAC game. This
    is the half of the guardrail that protects an actual decision."""
    query = build_query(["Recycling Tanks"], "glsmac")
    assert '?eng = "smac"' in query
    assert '?eng = "glsmac"' in query
    assert '?eng = "thinker"' not in query


def test_stock_smac_needs_no_second_engine_clause() -> None:
    """smac facts are legitimate everywhere, so the disjunction collapses."""
    query = build_query(["Recycling Tanks"], "smac")
    assert query.count("?eng = ") == 1


def test_the_query_uses_a_disjunction_not_values() -> None:
    """Quipu's SPARQL engine rejects both VALUES and FILTER(?x IN (…)) with
    'unsupported graph pattern' / 'unsupported FILTER expression', though
    knowledge-architecture.md specifies VALUES for this batched query. A ||
    chain is the equivalent that works."""
    query = build_query(["Recycling Tanks", "Energy Bank"], "thinker")
    assert "VALUES" not in query
    assert " IN (" not in query
    assert '?label = "Recycling Tanks" || ?label = "Energy Bank"' in query


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


def test_canonical_facts_are_not_annotated() -> None:
    """Tagging every canonical fact would be noise on the common case."""
    row = {"label": "Recycling Tanks", "cost": 4, "effect": "Bonus Resources", "tier": "canonical"}
    assert "[" not in format_row(row)


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


def test_retrieval_is_bounded_to_the_action_space() -> None:
    """Budget discipline: the prompt grows with the choices on offer, not with
    the rulebook."""
    retriever = FakeQuipu([])
    retriever.retrieve(view("Recycling Tanks", "Energy Bank"))
    query = retriever.queries[0]
    assert query.count("?label = ") == 2


def test_one_batched_query_per_decision() -> None:
    """Not one call per action — that is the difference between a bounded
    per-turn cost and a linear one."""
    retriever = FakeQuipu([])
    retriever.retrieve(view("A", "B", "C", "D"))
    assert len(retriever.queries) == 1


def test_the_limit_caps_the_query_not_just_the_result() -> None:
    retriever = FakeQuipu([], limit=2)
    retriever.retrieve(view("A", "B", "C"))
    assert retriever.queries[0].count("?label = ") == 2


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
    assert retriever.queries[0].count("?label = ") == 1


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
