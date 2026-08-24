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

**The batched action-space query uses ``VALUES``**, as
``docs/knowledge-architecture.md`` specifies. It did not always: quipu 0.3.11
rejected both ``VALUES`` and ``FILTER(?x IN (…))`` with ``unsupported graph
pattern`` / ``unsupported FILTER expression``, so this built a ``||``
disjunction instead. Both landed upstream in **quipu 0.3.13** (quipu #51, #52)
and the workaround is gone.

That is a **minimum-version requirement**, not a preference: a store older than
0.3.13 rejects the query outright. The failure is safe — the retriever raises,
the knowledge seam degrades the decision rather than stalling the turn
(``knowledge.py``) — but it is grounding lost on every decision, so the version
is worth stating rather than discovering.

``VALUES`` is also the construct that describes what is meant. The disjunction
emitted one comparison per label per variable, so the ``FILTER`` grew linearly
with the turn's action space — the exact prompt-bounded path ``build_query`` is
trying to keep tight. An inline relation joins instead of filtering, so the
engine can seed the BGP from it rather than scanning the graph and discarding
afterwards.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, Final

from ..contract import WorldView
from ..grounding_evidence import DEFAULT_GRAPH, Consultation
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


#: Short forms for node families that already have a TTL prefix, so a citable id matches what
#: the graph file calls the node. Anything not listed derives its prefix from the IRI path,
#: which is what keeps this from needing an edit every time a node type is added — the first
#: version hardcoded four families and silently emitted full IRIs for everything else.
_IRI_PREFIXES: Final[dict[str, str]] = {
    "facility": "fac",
    "source": "src",
    "component": "comp",
    "reactor": "rct",
    "ability": "abil",
    "chassis": "chas",
    "social": "soc",
    "terraform": "terra",
    "resource": "res",
}


def fact_id(iri: str) -> str:
    """A citable id that is also a pointer into the datalinks graph.

    The node's own IRI, compacted — ``unit:formers``, ``soc:politics-police-state``. Compact
    because a citation is repeated in the prompt and then again in the answer, and a full IRI is
    both token-expensive and easy for a model to mistype.

    Deliberately NOT derived from the label, which was the first implementation and was wrong: a
    label-derived slug looks like an identifier while pointing at nothing, so a cited fact could
    not be traced back to the node that produced it, nor to the source that node came from.
    Every fact therefore carries a pointer into datalinks by construction, and a citation can be
    resolved and re-verified rather than merely counted.
    """
    if not iri.startswith(NAMESPACE):
        # Not ours. Still a pointer, so do not silently rewrite it.
        return iri
    rest = iri[len(NAMESPACE) :]
    family, _, name = rest.partition("/")
    if not name:
        return rest
    return f"{_IRI_PREFIXES.get(family, family)}:{name}"


def _values(variable: str, values: list[str]) -> str:
    """``VALUES ?v { "a" "b" }`` — an inline relation, one line.

    Still escaped. The literals are action names from the adapter either way, and moving them
    out of a ``FILTER`` and into a pattern does not make the game's own data safe to paste into
    a query.
    """
    return f"VALUES ?{variable} {{ " + " ".join(f'"{escape(v)}"' for v in values) + " }"


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

    Two separate ``VALUES`` blocks rather than one over both variables: the pair is a cross
    product — every offered label, in either legitimate engine plane — and a single block would
    have to enumerate it row by row, which is the linear growth this replaced. They lead the
    group so the engine can seed the BGP from them; quipu joins a ``VALUES`` the same either
    side of the pattern, so this is about intent being legible rather than about a rewrite.

    Needs quipu >= 0.3.13. Older stores reject ``VALUES`` outright — see the module docstring.
    """
    engines = [UNIVERSAL_ENGINE] if engine == UNIVERSAL_ENGINE else [UNIVERSAL_ENGINE, engine]
    return f"""PREFIX smac: <{NAMESPACE}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?f ?label ?effect ?role ?tier ?maint ?src WHERE {{
  {_values("label", labels)}
  {_values("eng", engines)}
  ?f rdfs:label ?label ;
     smac:sourcedFrom ?src ;
     smac:ruleTier ?tier ;
     smac:appliesToEngine ?eng
  OPTIONAL {{ ?f smac:effectText ?effect }}
  OPTIONAL {{ ?f smac:role ?role }}
  OPTIONAL {{ ?f smac:maintenance ?maint }}
}}"""


def build_turn_query(engine: str) -> str:
    """Load the rulebook once at the turn boundary for local decision filtering.

    ``/decide`` can be called hundreds of times in one turn.  The action labels are not known
    when ``/turn`` arrives, so the bounded operation at that boundary is one engine-scoped read
    of the dedicated NA dataset, followed by in-process label selection for every decision.
    """
    engines = [UNIVERSAL_ENGINE] if engine == UNIVERSAL_ENGINE else [UNIVERSAL_ENGINE, engine]
    return f"""PREFIX smac: <{NAMESPACE}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?f ?label ?effect ?role ?tier ?maint ?src WHERE {{
  {_values("eng", engines)}
  ?f rdfs:label ?label ;
     smac:sourcedFrom ?src ;
     smac:ruleTier ?tier ;
     smac:appliesToEngine ?eng
  OPTIONAL {{ ?f smac:effectText ?effect }}
  OPTIONAL {{ ?f smac:role ?role }}
  OPTIONAL {{ ?f smac:maintenance ?maint }}
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
        limit: int = 0,
        timeout: float = 2.0,
        token_budget: int = 0,
        query_labels: int = 64,
        dataset: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # A named dataset is the tenancy boundary.  Omitting it preserves local
        # single-store development, while the shipped NA config names the
        # dedicated graph explicitly.
        self.dataset = dataset
        self.engine = engine
        #: Ceiling on facts KEPT, applied to what the store returned. 0 disables it, which is
        #: now the default: `token_budget` is the bound this layer is designed to have (it
        #: bounds prompt SIZE, which is the thing that actually costs, and it records what it
        #: shed). A count cap was doing that job in a place where nothing could see it.
        self.limit = limit
        #: Ceiling on labels put into ONE SPARQL disjunction — a guard on query shape, not on
        #: how much grounding a decision may have. Deliberately generous: no realistic action
        #: space reaches it, and when it does bite, the labels land in ``Grounding.shed``
        #: rather than vanishing.
        self.query_labels = query_labels
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
        self._turn_rows: dict[tuple[int, int], list[dict[str, Any]]] = {}
        #: The same fetch, kept as *evidence* rather than as data: what was asked, of which
        #: graph, and what came back. Yupana advises on whether a decision was grounded and it
        #: cannot see this call, so a consultation nobody recorded is indistinguishable from one
        #: that never happened (``grounding_evidence.py``). Keyed identically to ``_turn_rows``
        #: so the evidence and the facts it describes can never drift apart.
        self._turn_consultations: dict[tuple[int, int], Consultation] = {}
        self._turn_lock = threading.Lock()

    def prime_turn(self, turn: int, faction_id: int | None) -> int:
        """Fetch once for a faction's turn; never create an unscoped fog cache.

        Also records the consultation as evidence. A failed fetch is recorded too, and that
        is the point of doing it here rather than at the call site: the caller turns the
        exception into a degraded turn announcement, so by the time anything else can look,
        "the graph was unreachable" and "the graph was never asked" have collapsed into the
        same silence. Yupana distinguishes them (``transport-error`` vs ``missing``) only if
        the producer keeps them distinct at the one moment it still knows which happened.
        """
        if faction_id is None:
            raise ValueError("faction_id is required to prime Quipu grounding")
        query = build_turn_query(self.engine)
        try:
            rows = self.query(query)
        except Exception:
            self._remember(turn, faction_id, None, query)
            raise
        self._remember(turn, faction_id, rows, query)
        with self._turn_lock:
            self._turn_rows[(turn, faction_id)] = rows
            # Enough for retries and overlapping boundaries, without retaining a campaign.
            while len(self._turn_rows) > 8:
                self._turn_rows.pop(next(iter(self._turn_rows)))
        return len(rows)

    def _remember(
        self,
        turn: int,
        faction_id: int,
        rows: list[dict[str, Any]] | None,
        query: str,
    ) -> None:
        """Store what this consultation was, in the three states Yupana can tell apart.

        ``rows is None`` means the query never completed — deliberately not the same as an
        empty result. A boolean here would report a store that was down and a store that has
        no rules for this engine as the same fact, and they have opposite fixes.

        The entities are the fact IRIs (``?f``), which is what makes "consulted and got
        nothing" auditable: a reader can re-run ``query`` against ``graph`` and compare.
        """
        if rows is None:
            outcome: str = "transport-error"
            entities: tuple[str, ...] = ()
        else:
            entities = tuple(str(row["f"]) for row in rows if row.get("f"))
            outcome = "used" if entities else "empty"
        consultation = Consultation(
            graph=self.dataset or DEFAULT_GRAPH,
            query=query,
            entities=entities,
            turn=turn,
            outcome=outcome,  # type: ignore[arg-type]
            # Yupana compares this against the reference as a string. Normalising here rather
            # than at each use is what stops one caller binding "1" and another binding 1 —
            # a mismatch Yupana reports as `unresolved`, which reads as corrupt evidence.
            faction_id=str(faction_id),
            captured_at=int(time.time()),
        )
        with self._turn_lock:
            self._turn_consultations[(turn, faction_id)] = consultation
            while len(self._turn_consultations) > 8:
                self._turn_consultations.pop(next(iter(self._turn_consultations)))

    def consultation_for(self, turn: int, faction_id: int | None) -> Consultation | None:
        """The evidence for this faction's turn, or ``None`` if it was never primed.

        Fog-scoped like every other read here: an unscoped lookup would hand one faction the
        evidence of another's consultation, and the resulting record would look entirely
        normal (``turns.view`` is fail-closed for the same reason).
        """
        if faction_id is None:
            return None
        with self._turn_lock:
            return self._turn_consultations.get((turn, faction_id))

    def query(self, sparql: str) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"{self.base_url}/query",
            data=json.dumps(
                # Quipu's `graph` parameter accepts either a graph IRI or a named dataset IRI.
                {"query": sparql, **({"graph": self.dataset} if self.dataset else {})}
            ).encode(),
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
        # Subjects first, then the action labels. A surface that names a subject is asking a
        # question ABOUT it — every option concerns the same entity — so if the budget or the
        # limit has to drop something, the subject is the last thing to go. Surfaces that name
        # no subject are unaffected, which is every surface that predates this.
        candidates = list(world_view.subjects or []) + [a.action for a in world_view.action_space]
        labels = list(dict.fromkeys(candidates))
        # The query bound is a guard against an unbounded SPARQL disjunction, NOT the fact
        # limit — those were the same number until na-dhs, and conflating them cost 13 of 20
        # available facts on a 48-option decision while buying no latency at all (measured:
        # 12, 24 and 48 labels all returned in ~1151 ms against the same store).
        asked = labels[: self.query_labels]
        unasked = labels[self.query_labels :]
        if not asked:
            return Grounding()
        faction_id = getattr(world_view, "faction_id", None)
        with self._turn_lock:
            primed = self._turn_rows.get((world_view.turn, faction_id))
        if self.dataset:
            # A configured dataset is the production path.  Missing `/turn` or missing faction
            # attribution must degrade visibly instead of silently multiplying graph requests
            # on the game thread or borrowing another faction's cache.
            if faction_id is None:
                raise RuntimeError("faction_id is required for cached Quipu grounding")
            if primed is None:
                raise RuntimeError("Quipu grounding was not primed for this faction and turn")
            rows = primed
        else:
            rows = primed if primed is not None else self.query(build_query(asked, self.engine))
        # Preserve action-space order so the prompt reads in the order the
        # engine offered the choices, not in whatever order the store returns.
        by_label = {str(r.get("label")): r for r in rows}
        found = [label for label in asked if label in by_label]
        # The count limit applies HERE, to what the store actually returned — not upstream to
        # what we were willing to ask about. Applied to candidates it silently excluded whole
        # CATEGORIES rather than a tail: action spaces list units before facilities, so a cap
        # of 12 on a 48-option base.production decision grounded seven units and not one
        # facility, on a decision that is about facilities.
        matched = found[: self.limit] if self.limit else found
        # Two ways an option arrives unargued, and they are kept apart because the remedies
        # are opposite: `unmatched` is a gap in the graph, `shed` is a bound of ours.
        unmatched = tuple(label for label in asked if label not in by_label)
        shed = tuple(found[self.limit :]) + tuple(unasked) if self.limit else tuple(unasked)
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
            return replace(grounding, fact_ids=ids, unmatched=unmatched, shed=shed)

        return Grounding(
            facts=tuple(f.text for f in facts),
            fact_ids=tuple(fact_id(node_of[label]) for label in matched),
            unmatched=unmatched,
            shed=shed,
        )
