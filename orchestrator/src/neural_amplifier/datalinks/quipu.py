"""Retrieval backed by a running Quipu store — K1's last piece, K2's first.

:class:`~neural_amplifier.datalinks.briefing.DatalinksRetriever` reads parsed
Python structs; this reads the graph. Both satisfy the same ``Retriever``
protocol, so swapping them is a constructor argument and the orchestrator never
learns which is in play.

The read-side filter is the half of the anti-masquerade guardrail that actually
protects a decision. Emission tags every fact with ``appliesToEngine``; if
retrieval does not *filter* on it, the tag is decoration and a Thinker
house-rule surfaces unchanged in a GLSMAC game. So the engine predicate is not
optional here, and there is no code path that omits it.

**Quipu's SPARQL engine does not support ``VALUES``**, which
``docs/knowledge-architecture.md`` specifies for the batched action-space
query, nor ``FILTER(?x IN (…))``. Both return ``unsupported graph pattern`` /
``unsupported FILTER expression``. A ``||`` disjunction is the equivalent that
works, and ``OPTIONAL`` works — verified against quipu 0.3.11.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

from ..contract import WorldView
from ..knowledge import Grounding
from .budget import Fact, apply_budget

NAMESPACE = "http://neuralamplifier.local/ontology/smac/"

#: Facts true for stock SMAC are legitimate in any engine; anything else must
#: match the engine in play. This pairing is the read-side guardrail.
UNIVERSAL_ENGINE = "smac"


def escape(value: str) -> str:
    """Escape a SPARQL string literal.

    Action names arrive from the adapter, so an unescaped quote is at best a
    parse error and at worst a query rewritten by the game's own data.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def fact_id(iri: str) -> str:
    """A citable id that is also a pointer into the datalinks graph.

    This is the node's own IRI, compacted against the known prefixes — ``unit:formers``,
    ``fac:recycling-tanks``, ``tech:centauri-ecology``. It is deliberately NOT derived from
    the label, which was the first implementation and was wrong: a label-derived slug looks
    like an identifier while pointing at nothing, so a cited fact could not be traced back to
    the node that produced it, nor to the source that node came from.

    Every fact therefore carries at least one pointer into datalinks by construction, and a
    citation can be resolved and re-verified rather than merely counted.
    """
    for prefix, base in (
        ("unit", f"{NAMESPACE}unit/"),
        ("fac", f"{NAMESPACE}facility/"),
        ("tech", f"{NAMESPACE}tech/"),
        ("src", f"{NAMESPACE}source/"),
    ):
        if iri.startswith(base):
            return f"{prefix}:{iri[len(base) :]}"
    # An IRI we do not have a prefix for is still a pointer; do not silently rewrite it.
    return iri


def _disjunction(variable: str, values: list[str]) -> str:
    """``?v = "a" || ?v = "b"`` — Quipu rejects both VALUES and FILTER IN."""
    return " || ".join(f'?{variable} = "{escape(v)}"' for v in values)


def build_query(labels: list[str], engine: str) -> str:
    """One batched query for exactly this turn's action space.

    Bounded by construction: the prompt grows with the actions on offer, not with the size
    of the rulebook.

    Cost and effect are OPTIONAL, and that is not defensive style — it is required for
    correctness. Facilities carry ``smac:cost`` and ``smac:effectText``; units carry
    ``smac:role`` and often no cost at all, because the engine derives a unit's price from
    its components. Requiring the facility-shaped predicates matched zero units, so the
    retriever silently grounded nothing about the very options a model is most likely to
    misunderstand — which is how a Colony Pod got built to grow the base that built it.
    """
    engines = [UNIVERSAL_ENGINE] if engine == UNIVERSAL_ENGINE else [UNIVERSAL_ENGINE, engine]
    return f"""PREFIX smac: <{NAMESPACE}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?f ?label ?effect ?role ?tier ?maint ?src WHERE {{
  ?f rdfs:label ?label ;
     smac:sourcedFrom ?src ;
     smac:ruleTier ?tier ;
     smac:appliesToEngine ?eng
  OPTIONAL {{ ?f smac:effectText ?effect }}
  OPTIONAL {{ ?f smac:role ?role }}
  OPTIONAL {{ ?f smac:maintenance ?maint }}
  FILTER(({_disjunction("label", labels)}) && ({_disjunction("eng", engines)}))
}}"""


def format_row(row: dict[str, Any]) -> str:
    """One fact a model can act on.

    The tier is carried into the prompt rather than dropped: a house-rule fact presented as
    canonical is the failure the whole plane guards against, and the model is entitled to
    know which it is looking at.

    Cost is deliberately NOT included, even though the graph has it. The action space
    already carries an authoritative cost: the adapter normalises the rulebook's "rows" into
    minerals using the faction's own cost factor, which varies by difficulty. The graph holds
    the raw rulebook value. Emitting both put "cost 4" from grounding beside "cost 44" from
    the action space in the same prompt — the same two-units-in-one-payload error that
    already produced bad arithmetic once, and worse here because the two numbers disagree
    about the same thing.

    So the division of labour is explicit: the action space says what an option COSTS, and
    grounding says what it DOES. Upkeep stays, because nothing else reports it.
    """
    parts: list[str] = [str(row.get("label"))]
    maint = row.get("maint")
    if maint:
        parts.append(f"upkeep {maint}/turn")
    # role is a unit's purpose, effect is a facility's — a row carries one or the other.
    for key in ("role", "effect"):
        if row.get(key):
            parts.append(str(row[key]))
    tier = row.get("tier")
    src = row.get("src")
    # Tier and source travel with the fact. A house-rule stat presented as canonical is the
    # failure this whole plane exists to prevent, and a fact whose origin cannot be named is
    # not auditable — the model is entitled to know both.
    # The tier is annotated only when it is NOT canonical: tagging every canonical fact
    # is noise on the common case, and noise is what stops people reading the tag that
    # matters. The SOURCE is annotated always — a fact whose origin cannot be named is not
    # auditable, and every fact is required to carry at least one pointer into datalinks.
    tag = tier if tier and tier != "canonical" else ""
    if src:
        origin = fact_id(str(src))
        tag = f"{tag} · {origin}" if tag else origin
    if tag:
        parts.append(f"[{tag}]")
    return "; ".join(parts)


class QuipuRetriever:
    """Queries a ``quipu-server`` over REST.

    Failures raise; the knowledge seam turns them into a degraded decision
    rather than a stalled turn (``knowledge.py``). Nothing is caught here,
    because a retriever that swallows its own errors is indistinguishable from
    one that found nothing.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:3030",
        engine: str = "thinker",
        limit: int = 12,
        timeout: float = 2.0,
        token_budget: int = 0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.engine = engine
        self.limit = limit
        #: Approximate token ceiling for grounding, 0 to disable. Bounding
        #: the count alone says nothing about prompt size when one fact is
        #: a paragraph (``budget.py``).
        self.token_budget = token_budget
        #: A hung request would block the decision loop even though the seam
        #: catches exceptions — a timeout is what makes "degrade, never stall"
        #: true rather than aspirational.
        self.timeout = timeout
        # Quipu runs on localhost; an ambient HTTPS_PROXY would otherwise send
        # loopback traffic through a proxy that cannot route it.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def query(self, sparql: str) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"{self.base_url}/query",
            data=json.dumps({"query": sparql}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with self._opener.open(request, timeout=self.timeout) as response:
            payload = json.loads(response.read())
        if "error" in payload:
            raise RuntimeError(f"quipu rejected the query: {payload['error']}")
        rows: list[dict[str, Any]] = payload.get("rows", [])
        return rows

    def retrieve(self, world_view: WorldView) -> Grounding:
        labels = list(dict.fromkeys(a.action for a in world_view.action_space))[: self.limit]
        if not labels:
            return Grounding()
        rows = self.query(build_query(labels, self.engine))
        # Preserve action-space order so the prompt reads in the order the
        # engine offered the choices, not in whatever order the store returns.
        by_label = {str(r.get("label")): r for r in rows}
        matched = [label for label in labels if label in by_label]
        node_of = {label: str(by_label[label].get("f", "")) for label in matched}
        facts = [Fact(format_row(by_label[label]), kind="rule") for label in matched]

        if self.token_budget > 0:
            budgeted = apply_budget(facts, self.token_budget)
            grounding = budgeted.grounding()
            kept = set(grounding.facts)
            # Ids must stay aligned with the facts that SURVIVED the budget, not with
            # everything retrieved — otherwise a dropped fact still looks citable and
            # utilisation is computed against a prompt the model never saw.
            ids = tuple(
                fact_id(node_of[label])
                for label, fact in zip(matched, facts, strict=True)
                if fact.text in kept
            )
            return replace(grounding, fact_ids=ids)

        return Grounding(
            facts=tuple(f.text for f in facts),
            fact_ids=tuple(fact_id(node_of[label]) for label in matched),
        )
