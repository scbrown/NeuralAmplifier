"""Hank-shaped policy guards — ``docs/knowledge-architecture.md`` §Hank's five roles.

Role (c) is the gameplay policy guardrail: evaluate policies against live state plus proposed
orders and return ``{violations, advisories}``. Two properties of that role shape everything
here:

* **It complements ``action_space``, never replaces it.** Legality is the engine's job. A guard
  only subtracts or annotates orders that are *already legal*.
* **A guard that is down allows** (``knowledge.py``). The engine's own legality check still
  stands behind it, and a guard failure that silently blocked every move would stall a game to
  enforce a policy nobody could read.

What lands here first is citation integrity, because it is the one policy that needs no hot
game-state graph (role (d), not built) — only the facts that were offered and the graph they
came from. It exists because grounding is otherwise unfalsifiable: a decision that cites
nothing and a decision that cites a fact nobody supplied both look like reasoning.

This is a local stand-in for Hank's ``POST /guard`` surface, not Hank itself. When that lands,
the verdict shape and warn/deny semantics are already what it returns, so swapping the
implementation should not change the record.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .contract import Orders, WorldView
from .directives import entities_shown
from .knowledge import Ruling, grounded_ids

#: Resolves a fact id to whether it exists in the datalinks graph. Injected so the policy is
#: testable without a store, and so the same policy can run against Quipu, a local TTL, or a
#: fixture without knowing which.
Resolver = Callable[[str], bool]


class CitationGuard:
    """Checks that the facts a brain cited were really offered, and really exist.

    Three distinct failures, deliberately given three different advisories rather than one
    generic "bad citation" — they have different causes and different fixes:

    ``unoffered``
        The brain cited an id that was never put in front of it. That is a fabricated
        citation, and it matters because the provenance block would otherwise assert that
        something informed the decision when nothing did.
    ``unresolvable``
        The id was offered, but does not resolve in the graph. That is not the model's fault;
        it means something emitted a pointer to a node that is missing — retrieval, or a
        directive naming an entity that no longer exists. Either way a datalinks integrity
        problem rather than a reasoning one.
    ``uncited``
        Facts were offered and none were cited. Not an error — a decision can legitimately
        turn on state rather than rules — but worth surfacing, because a run where this is
        always true is paying for retrieval nobody reads. Scoped to *grounding* alone: it is a
        statement about retrieval, and directive entities are not retrieved.

    Verdict is **warn**, never deny. A suspect justification does not make an order illegal,
    and denying a legal move because its reasoning was sloppy would break the game to make a
    point about bookkeeping.
    """

    def __init__(self, resolver: Resolver | None = None) -> None:
        #: Absent means "cannot check existence" — the guard then verifies only that a
        #: citation was offered, and does not manufacture ``unresolvable`` findings it has no
        #: evidence for.
        self.resolver = resolver

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        # Two id spaces that are deliberately the same. A directive's ``entities`` *are*
        # grounding fact ids — that is what makes the multi-hop walk work — but they reach the
        # brain through the directives block. Reading the offered set out of grounding alone
        # made every such citation look fabricated (na-zgz).
        #
        # They stay two names past this line because only ``grounded`` is about retrieval, and
        # the ``uncited`` advisory below is a statement about retrieval.
        grounded = grounded_ids(world_view)
        offered = grounded | entities_shown(world_view)
        cited = list(dict.fromkeys(orders.cited))

        advisories: list[str] = []

        unoffered = [c for c in cited if c not in offered]
        if unoffered:
            advisories.append(
                "cited facts that were never offered: " + ", ".join(sorted(unoffered))
            )

        if self.resolver is not None:
            checked = [c for c in cited if c in offered]
            unresolvable = [c for c in checked if not self._safe_resolve(c)]
            if unresolvable:
                advisories.append(
                    "cited facts that do not resolve in the graph: "
                    + ", ".join(sorted(unresolvable))
                )

        if grounded and not cited:
            advisories.append(
                f"{len(grounded)} fact(s) offered, none cited — grounding may be unread"
            )

        return Ruling(verdict="warn" if advisories else "allow", advisories=tuple(advisories))

    def _safe_resolve(self, fact_id: str) -> bool:
        """A resolver that throws must not turn into a violation.

        The guard's job is to report on the decision, not to fail one because a store blinked.
        ``knowledge.rule`` would already absorb the exception into a degraded allow, but that
        loses the advisories we did compute — so treat an unresolvable check as "cannot say".
        """
        assert self.resolver is not None
        try:
            return self.resolver(fact_id)
        except Exception:  # noqa: BLE001 — see docstring
            return True


def quipu_resolver(retriever: object, known: Iterable[str] | None = None) -> Resolver:
    """Resolve fact ids against a set of known graph ids.

    Takes a precomputed set rather than querying per fact: a guard runs on every decision, and
    one round trip per citation would put the store on the critical path of a turn for a check
    that is only ever advisory.
    """
    ids = set(known or ())
    return lambda fact_id: fact_id in ids
