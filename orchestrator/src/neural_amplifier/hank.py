"""Hank-shaped policy guards — ``docs/knowledge-architecture.md`` §Hank's five roles.

Role (c) is the gameplay policy guardrail: evaluate policies against live state plus proposed
orders and return ``{violations, advisories}``. Two properties of that role shape everything
here:

* **It complements ``action_space``, never replaces it.** Legality is the engine's job. A guard
  only subtracts or annotates orders that are *already legal*.
* **A guard that is down allows** (``knowledge.py``). The engine's own legality check still
  stands behind it, and a guard failure that silently blocked every move would stall a game to
  enforce a policy nobody could read.

Two policies live here, and both are chosen for the same reason: they need no hot game-state
graph (role (d), which is not built). :class:`CitationGuard` needs only the facts that were
offered; :class:`StateGuard` needs only numbers the world view already declares.

Citation integrity exists because grounding is otherwise unfalsifiable: a decision that cites
nothing and a decision that cites a fact nobody supplied both look like reasoning. State
checking exists because the agent-brain pivot gave the brain a memory and the adapter a replay,
and both can act on a board that has moved.

This is a local stand-in for Hank's ``POST /guard`` surface, not Hank itself. When that lands,
the verdict shape and warn/deny semantics are already what it returns, so swapping the
implementation should not change the record.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .contract import Orders, WorldView
from .knowledge import Ruling

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
        it means retrieval emitted a pointer to a node that is missing, which is a datalinks
        integrity problem.
    ``uncited``
        Facts were offered and none were cited. Not an error — a decision can legitimately
        turn on state rather than rules — but worth surfacing, because a run where this is
        always true is paying for retrieval nobody reads.

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
        offered = self._offered(world_view)
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

        if offered and not cited:
            advisories.append(
                f"{len(offered)} fact(s) offered, none cited — grounding may be unread"
            )

        return Ruling(verdict="warn" if advisories else "allow", advisories=tuple(advisories))

    def _offered(self, world_view: WorldView) -> set[str]:
        """Fact ids the orchestrator put in front of the brain.

        Read from the world view's ``grounding`` block, which is where ``knowledge.py`` says
        the orchestrator injects retrieved facts. A world view with no grounding yields an
        empty set, which correctly makes every citation ``unoffered``.
        """
        grounding = world_view.grounding or []
        return {gid for gid in (self._id_of(line) for line in grounding) if gid}

    @staticmethod
    def _id_of(line: str) -> str:
        """Facts are injected as ``"<id> <text>"``; the id is the first token.

        Kept deliberately dumb. A richer encoding would need the contract to carry structured
        grounding, and that is a contract change rather than a guard's business.
        """
        return line.split(" ", 1)[0].strip() if line else ""

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


class StateGuard:
    """Checks a chosen order against the state it is about to be applied to.

    Role (c)'s other half, and the one the agent-brain pivot made urgent. While a model answered
    in a couple of seconds with the game blocked, the board could not move between the snapshot
    and the apply. Two things changed that:

    * an agent takes as long as it likes, and a Claude Code session persists across a whole
      game — so it can carry a *belief* about a base from twenty turns ago and reason from
      memory rather than from the world view in front of it, which a stateless model call
      could not do;
    * the adapter caches one decision per base-turn and replays it for the engine's later
      calls, and the board has genuinely moved on by then.

    What it can check today is bounded, and the bound is worth stating: with no hot board graph
    (role (d), not built) this reasons only over numbers the world view already declares. That
    is less than Hank will do and it is not nothing — every check here is arithmetic on figures
    the adapter published, so a failure is a fact rather than a guess.

    Precedence is unchanged. ``action_space`` is the legality gate and runs first; this only
    subtracts from what is already legal, and it never adds.
    """

    name = "state"

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        stripped: list[str] = []
        advisories: list[str] = []
        metrics = world_view.metrics or {}
        by_id = {a.id: a for a in world_view.action_space}

        for choice in orders.choices:
            action = by_id.get(choice.action_id)
            if action is None:
                # validate() runs first and removes these, so reaching here means the two
                # disagree. Not this guard's job to fix, and not its job to hide either.
                continue
            for metric, delta in (action.effects or {}).items():
                current = metrics.get(metric)
                if current is None or delta >= 0:
                    # An unreported metric must read as *uncheckable*, never as satisfied.
                    # Silence here is the honest outcome: the alternative is inventing a
                    # baseline and denying a legal move on the strength of it.
                    continue
                projected = current + delta
                if projected < 0:
                    stripped.append(choice.action_id)
                    advisories.append(
                        f"{choice.action_id} spends {abs(delta):g} {metric} but only "
                        f"{current:g} is available — the state moved since this was offered"
                    )

        # Directives are weighed, never obeyed. A standing plan losing to an urgent move is the
        # mechanism working: priorities exist so a decision can outrank a plan rather than
        # either ignoring it or treating it as law. So a violated directive is an advisory that
        # lands on the record, and never a denial.
        chosen = {c.action_id for c in orders.choices}
        for tradeoff in world_view.tradeoffs or []:
            if tradeoff.action_id in chosen and tradeoff.would_violate:
                advisories.append(
                    f"{tradeoff.action_id} breaks directive {tradeoff.directive_id} "
                    f"(priority {tradeoff.directive_priority}): {tradeoff.metric} "
                    f"{tradeoff.delta:+g} → {tradeoff.projected:g}"
                )

        if stripped:
            return Ruling(
                verdict="deny",
                stripped=tuple(dict.fromkeys(stripped)),
                advisories=tuple(advisories),
                reason="order is unaffordable against current state",
            )
        return Ruling(verdict="allow", advisories=tuple(advisories))


class GuardChain:
    """Runs several guards and merges their rulings.

    Deny wins. A chain in which one guard allows and another denies has denied — otherwise the
    order of registration would silently decide policy, which is the kind of configuration bug
    that is invisible until it matters.

    Degradation propagates rather than being swallowed: if any guard was down, the ruling says
    so, because a record that reports a clean pass from a guard that never ran is worse than
    one that reports nothing.
    """

    name = "chain"

    def __init__(self, *guards: object) -> None:
        self.guards = [g for g in guards if g is not None]

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        stripped: list[str] = []
        advisories: list[str] = []
        reasons: list[str] = []
        degraded = False
        for guard in self.guards:
            ruling = guard.rule(orders, world_view)  # type: ignore[attr-defined]
            stripped.extend(ruling.stripped)
            advisories.extend(ruling.advisories)
            degraded = degraded or ruling.degraded
            if ruling.reason:
                reasons.append(ruling.reason)
        return Ruling(
            verdict="deny" if stripped else "allow",
            stripped=tuple(dict.fromkeys(stripped)),
            advisories=tuple(advisories),
            degraded=degraded,
            reason="; ".join(reasons) or None,
        )
