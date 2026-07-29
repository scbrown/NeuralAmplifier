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


def _disjunction(variable: str, values: list[str]) -> str:
    """``?v = "a" || ?v = "b"`` — Quipu rejects both VALUES and FILTER IN."""
    return " || ".join(f'?{variable} = "{escape(v)}"' for v in values)


def build_query(labels: list[str], engine: str) -> str:
    """One batched query for exactly this turn's action space.

    Bounded by construction: the prompt grows with the actions on offer, not
    with the size of the rulebook.
    """
    engines = [UNIVERSAL_ENGINE] if engine == UNIVERSAL_ENGINE else [UNIVERSAL_ENGINE, engine]
    return f"""PREFIX smac: <{NAMESPACE}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?label ?cost ?effect ?tier ?maint WHERE {{
  ?f rdfs:label ?label ;
     smac:cost ?cost ;
     smac:effectText ?effect ;
     smac:ruleTier ?tier ;
     smac:appliesToEngine ?eng
  OPTIONAL {{ ?f smac:maintenance ?maint }}
  FILTER(({_disjunction("label", labels)}) && ({_disjunction("eng", engines)}))
}}"""


def format_row(row: dict[str, Any]) -> str:
    """One fact a model can act on.

    The tier is carried into the prompt rather than dropped: a house-rule fact
    presented as canonical is the failure the whole plane guards against, and
    the model is entitled to know which it is looking at.
    """
    parts = [f"{row.get('label')} — cost {row.get('cost')}"]
    maint = row.get("maint")
    if maint:
        parts.append(f"upkeep {maint}/turn")
    if row.get("effect"):
        parts.append(str(row["effect"]))
    tier = row.get("tier")
    if tier and tier != "canonical":
        parts.append(f"[{tier}]")
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
        facts = [
            Fact(format_row(by_label[label]), kind="rule") for label in labels if label in by_label
        ]
        if self.token_budget <= 0:
            return Grounding(facts=tuple(f.text for f in facts))
        return apply_budget(facts, self.token_budget).grounding()
