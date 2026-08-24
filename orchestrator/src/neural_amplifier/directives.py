"""Issuing, keeping and measuring directives.

Three jobs, kept in one place because they are one loop:

* **Accept** what a decision issued, rejecting anything that could never be checked.
* **Keep** it across decisions, and drop it when its horizon passes.
* **Measure** it against each new world view, so a later decision sees the standing intent and
  where the faction actually stands against it.

The loop matters more than any of the parts. A long-horizon decision that reasons well about a
tech path produces nothing durable today — the next ``base.production`` call starts from
scratch. This is the wire from one to the other, and the measurement is what stops it from
becoming a place to accumulate untestable ambition.

Degrades like everything else on the knowledge path (invariant 9): a store that cannot be read
means a less-informed decision, never a stalled turn.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .contract import Directive, DirectiveStatus, Tradeoff, WorldView
from .metrics import VOCABULARY, known

#: Metrics that measure a *rate* of change in another metric, used to turn a one-off cost into
#: turns of setback. "81 credits" means little on its own; "21 turns of saving" is the number a
#: decision can weigh against finishing production seven turns early.
RATE_OF: Final[dict[str, str]] = {
    "energy_reserves": "energy_income",
    "minerals_remaining": "mineral_surplus",
}

#: How close counts as holding. A ``hold`` directive on a quantity that moves every turn would
#: otherwise be violated by noise, and a directive that is always violated gets ignored — by a
#: model as surely as by a person.
HOLD_TOLERANCE = 0.10


class DirectiveError(ValueError):
    """A directive that could not be accepted. Never raised at decision time."""


def validate(directive: Directive) -> None:
    """Reject a directive that cannot be measured.

    Raised only where a directive is *issued*, never where one is applied. The distinction is
    the whole point: an unmeasurable directive is caught while the decision that wrote it is
    still in the loop and could be told why, instead of silently failing to steer anything for
    the rest of the game.
    """
    if not directive.id:
        raise DirectiveError("a directive needs an id; it is how a later decision refers to it")
    if not known(directive.metric):
        raise DirectiveError(
            f"unknown metric {directive.metric!r}; a directive may only reference "
            f"{', '.join(sorted(VOCABULARY))}"
        )
    if not directive.is_relative() and directive.target is None:
        raise DirectiveError(
            f"{directive.comparator!r} needs a target — without one there is nothing to compare"
        )
    if (
        directive.starts_turn is not None
        and directive.horizon_turn is not None
        and directive.starts_turn > directive.horizon_turn
    ):
        raise DirectiveError(
            f"starts_turn {directive.starts_turn} is after horizon_turn "
            f"{directive.horizon_turn}; that directive could never be active"
        )


def accept(issued: Iterable[Directive], world_view: WorldView) -> tuple[list[Directive], list[str]]:
    """Take the valid directives from a response, and say why the rest were dropped.

    Returns the accepted directives and one rejection message per refusal. Rejections are
    returned rather than raised because a bad directive must not cost the decision it came
    with — the choice may be perfectly good even where the plan attached to it was not
    expressible.

    A relative directive gets its baseline stamped here, from the world view that issued it.
    That is deliberately not the model's job: it was shown the number, and asking it to repeat
    one it has already read is a chance to paraphrase it wrong.
    """
    accepted: list[Directive] = []
    rejected: list[str] = []
    measurements = world_view.metrics or {}
    for directive in issued:
        try:
            validate(directive)
        except DirectiveError as exc:
            rejected.append(f"directive {directive.id or '<no id>'!r} rejected: {exc}")
            continue
        stamped = directive.model_copy(
            update={
                "issued_turn": (
                    directive.issued_turn if directive.issued_turn is not None else world_view.turn
                ),
                "baseline": _baseline_for(directive, measurements),
            }
        )
        if stamped.is_relative() and stamped.baseline is None:
            # Accepting it would create a directive that is permanently unmeasurable: nothing
            # later can supply a baseline for a turn that has already passed.
            rejected.append(
                f"directive {directive.id!r} rejected: {directive.comparator!r} is measured "
                f"against the value at issue time, and {directive.metric!r} was not reported"
            )
            continue
        accepted.append(stamped)
    return accepted, rejected


def _baseline_for(directive: Directive, measurements: dict[str, float]) -> float | None:
    if directive.baseline is not None:
        return directive.baseline
    value = measurements.get(directive.metric)
    return float(value) if isinstance(value, int | float) else None


#: Directives at or above this priority are shown whether or not they relate to the decision.
#: Survival-level intent that a base-scope decision never sees is intent that cannot be served —
#: and there should only ever be one or two of these, so the cost is bounded.
ALWAYS_SHOW_PRIORITY = 9

#: How many directives a single decision may be shown. Chosen the way the retrieval budget was:
#: enough to carry the plan actually bearing on this choice, small enough that a base-scope
#: decision firing once per base per turn stays affordable.
RELEVANCE_LIMIT = 8


#: How far the search walks from the decision. Two hops is what the motivating chain needs:
#: an action spends energy credits (hop 0 pulls the saving directive), that directive names a
#: secret project (hop 1 pulls the plan for the project itself), and that plan may serve a
#: higher-order strategy on the same entity. Beyond that the connection is too weak to be worth
#: prompt space, and the cost grows with every step.
MAX_HOPS = 2


@dataclass(frozen=True)
class Hit:
    """A directive the search reached, and how it got there.

    ``via`` is not bookkeeping. A decision told only "here are three directives" has to guess why
    any of them are in front of it; told "hurrying spends energy_reserves → fund-weather-paradigm
    → fac:the-weather-paradigm → terraform-victory", it can see that the 81 credits are not
    abstractly valuable but are earmarked for a project serving a plan it can weigh. The path *is*
    the reasoning, so it travels with the directive.
    """

    directive: Directive
    hop: int
    via: str
    score: int


@dataclass(frozen=True)
class Selection:
    """Which directives a decision is shown, and what was left out.

    ``dropped`` exists because a silent cap is the failure mode that makes a plan look served
    when it was never read. If a directive was in force and the decision never saw it, that has
    to be visible in the record rather than inferred from the absence of a mention.
    """

    hits: list[Hit]
    dropped: list[str]

    @property
    def selected(self) -> list[Directive]:
        return [h.directive for h in self.hits]


def relevant(
    directives: Sequence[Directive],
    world_view: WorldView,
    entity_ids: Iterable[str] = (),
    limit: int = RELEVANCE_LIMIT,
    hops: int = MAX_HOPS,
) -> Selection:
    """The directives that bear on *this* decision, found by walking out from it.

    A game can accumulate hundreds of directives. Injecting all of them into every world view
    would cost more per decision than the whole grounding budget and bury the two that matter, so
    a directive has to be *retrieved* like a fact rather than broadcast like a setting.

    The walk starts from what the decision actually does and follows the links:

    * **Hop 0 — what this decision touches.** An action whose ``effects`` change a metric pulls
      every directive about that resource; an entity on offer pulls every directive naming it.
      This is the case the mechanism exists for: a base about to spend the energy credits a plan
      is saving.
    * **Hop 1+ — what those directives are for.** Each directive reached contributes its own
      ``entities``, which pull the directives about *them*. This is how spending energy credits
      surfaces not just "we are saving" but the project being saved for, and the higher-order
      plan that project serves.

    Expansion is through **entities only** after hop 0, deliberately. Following metrics
    transitively would pull every directive sharing a common measure — half the plan hangs off
    ``base_count`` — and turn a targeted walk into a broadcast with extra steps.

    Priority breaks ties rather than driving selection: a priority-10 directive about naval
    movement has no business in a research decision, while survival-level intent
    (``ALWAYS_SHOW_PRIORITY``) is shown regardless, because a plan nobody is told about cannot be
    followed.
    """
    live = [d for d in directives if _active(d, world_view.turn)]
    reported = set((world_view.metrics or {}).keys())

    # Hop 0 seeds: the resources this decision would move, and the entities it is about.
    touched: dict[str, str] = {}
    for action in world_view.action_space:
        for metric, delta in (action.effects or {}).items():
            if delta:
                touched.setdefault(
                    metric, f"{action.id} changes {metric} by {_delta(float(delta))}"
                )
    frontier_entities = {e for e in entity_ids if e}

    hits: dict[str, Hit] = {}
    unreached: list[str] = []

    def _record(directive: Directive, hop: int, via: str, score: int) -> bool:
        """First path wins. The shortest route is both the strongest and the clearest to read."""
        if directive.id in hits:
            return False
        hits[directive.id] = Hit(directive=directive, hop=hop, via=via, score=score)
        return True

    for directive in live:
        if directive.metric in touched:
            _record(directive, 0, touched[directive.metric], 6)
        elif frontier_entities & set(directive.entities):
            matched = sorted(frontier_entities & set(directive.entities))[0]
            _record(directive, 0, f"this decision concerns {matched}", 5)

    # Walk out: each directive reached offers its entities as the next frontier.
    for hop in range(1, hops + 1):
        frontier: dict[str, str] = {}
        for hit in list(hits.values()):
            if hit.hop != hop - 1:
                continue
            for entity in hit.directive.entities:
                frontier.setdefault(entity, hit.directive.id)
        if not frontier:
            break
        for directive in live:
            shared = sorted(set(directive.entities) & set(frontier))
            if shared:
                entity = shared[0]
                _record(directive, hop, f"{frontier[entity]} → {entity}", max(4 - hop, 1))

    # A directive the walk never reached is not shown, even when there is room. Padding the block
    # out to the limit with plans this decision cannot affect costs prompt space on every
    # decision and teaches the model that most of the block is noise — which is the fastest way
    # to have it stop reading the two entries that matter. Measured: with 16 directives in the
    # plan, half of a four-slot block went to entries whose own explanation read "not related to
    # this decision".
    #
    # The two exceptions earn their place. Survival-level intent is shown because a plan nobody
    # is told about cannot be followed. A directive whose metric this world view reports is shown
    # because it can at least be checked here, which is how a slow drift gets noticed by someone.
    for directive in live:
        if directive.id in hits:
            continue
        if directive.priority >= ALWAYS_SHOW_PRIORITY:
            _record(directive, 0, "survival-priority, shown regardless of relevance", 8)
        elif directive.metric in reported:
            _record(directive, hops + 1, f"{directive.metric} is measurable here", 1)
        else:
            unreached.append(directive.id)

    ranked = sorted(
        hits.values(),
        key=lambda h: (-h.score, h.hop, -h.directive.priority, -(h.directive.issued_turn or 0)),
    )
    return Selection(
        hits=ranked[:limit],
        dropped=[h.directive.id for h in ranked[limit:]] + unreached,
    )


def evaluate(
    directives: Sequence[Directive] | Sequence[Hit], world_view: WorldView
) -> list[DirectiveStatus]:
    """Measure each directive against this world view.

    Accepts either bare directives or the hits from :func:`relevant`; given hits, the path that
    reached each one travels through onto the status, because that path is what tells the
    decision why the directive is in front of it.

    Expired directives are excluded — a plan whose horizon has passed is history, and leaving it
    in front of a model as though it still applied would be actively misleading.
    """
    measurements = world_view.metrics or {}
    statuses: list[DirectiveStatus] = []
    for item in directives:
        directive = item.directive if isinstance(item, Hit) else item
        if not _active(directive, world_view.turn):
            continue
        status = _status(directive, measurements)
        if isinstance(item, Hit):
            status = status.model_copy(update={"via": item.via, "hop": item.hop})
        statuses.append(status)
    return statuses


def entities_shown(world_view: WorldView) -> set[str]:
    """Datalinks ids this world view put in front of the brain *via a directive*.

    A directive's ``entities`` are grounding fact ids — that shared id space is exactly what
    makes the multi-hop walk in :func:`relevant` work. But they arrive through the directives
    block, not the grounding block, so anything that reads "what was offered" out of grounding
    alone sees them as ids the model invented. Measured: three to four runs in five carried
    ``cited facts that were never offered: fac:the-weather-paradigm`` for an id the world view
    had genuinely shown (na-zgz).

    So the two readers of the offered set share this. What it deliberately does **not** do is
    make these ids part of grounding: they were not retrieved, nothing paid for them, and
    folding them into the utilisation denominator would move a retrieval metric every time the
    plan changed shape.
    """
    return {
        entity
        for status in (world_view.directives or [])
        for entity in status.directive.entities
        if entity
    }


def _expired(directive: Directive, turn: int) -> bool:
    return directive.horizon_turn is not None and turn > directive.horizon_turn


def _active(directive: Directive, turn: int) -> bool:
    """Whether a directive belongs in this turn's plan.

    Future checkpoints stay persisted but invisible. Deleting a not-yet-active checkpoint would
    collapse a staged plan to its first target on the first store read.
    """
    return (directive.starts_turn is None or turn >= directive.starts_turn) and not _expired(
        directive, turn
    )


def _status(directive: Directive, measurements: dict[str, float]) -> DirectiveStatus:
    raw = measurements.get(directive.metric)
    if not isinstance(raw, int | float):
        # Unmeasurable, and said so. The alternative — treating an absent metric as satisfied —
        # is how a directive mechanism turns into decoration nobody notices is broken.
        return DirectiveStatus(
            directive=directive,
            current=None,
            satisfied=None,
            detail=(
                f"{directive.metric} is not reported in this world view, so this directive "
                f"cannot be checked here"
            ),
        )

    current = float(raw)
    satisfied, detail = _compare(directive, current)
    return DirectiveStatus(directive=directive, current=current, satisfied=satisfied, detail=detail)


def _compare(directive: Directive, current: float) -> tuple[bool, str]:
    metric = directive.metric
    if directive.comparator == "at_least":
        target = float(directive.target or 0.0)
        return current >= target, f"{metric} is {_n(current)}, wanted at least {_n(target)}"
    if directive.comparator == "at_most":
        target = float(directive.target or 0.0)
        return current <= target, f"{metric} is {_n(current)}, wanted at most {_n(target)}"

    baseline = float(directive.baseline or 0.0)
    delta = current - baseline
    if directive.comparator == "increase":
        return delta > 0, f"{metric} is {_n(current)}, {_delta(delta)} since {_n(baseline)}"
    if directive.comparator == "decrease":
        return delta < 0, f"{metric} is {_n(current)}, {_delta(delta)} since {_n(baseline)}"

    span = abs(baseline) * HOLD_TOLERANCE
    return (
        abs(delta) <= span,
        f"{metric} is {_n(current)}, {_delta(delta)} from a held {_n(baseline)}",
    )


def tradeoffs(directives: Sequence[Directive], world_view: WorldView) -> list[Tradeoff]:
    """What each action would cost each standing directive.

    The half of the mechanism that makes a priority number usable. A directive says "save energy,
    priority 7"; without this, a base deciding whether to spend 81 credits has to guess what
    ignoring that costs. With it, the decision is between two quantified things.

    Only real conflicts are emitted — an action that does not touch a directive's metric produces
    nothing. That keeps the block empty on the great majority of decisions, which is both cheaper
    and a clearer signal: a non-empty ``tradeoffs`` means something is genuinely at stake.
    """
    measurements = world_view.metrics or {}
    out: list[Tradeoff] = []
    for action in world_view.action_space:
        effects = action.effects or {}
        if not effects:
            continue
        for directive in directives:
            delta = effects.get(directive.metric)
            if not isinstance(delta, int | float) or delta == 0:
                continue
            current = measurements.get(directive.metric)
            if not isinstance(current, int | float):
                # No current value means no projection. Reporting a delta with no baseline would
                # invite the model to treat the delta itself as the resulting level.
                continue
            projected = float(current) + float(delta)
            satisfied_now, _ = _compare(directive, float(current))
            satisfied_after, _ = _compare(directive, projected)
            out.append(
                Tradeoff(
                    action_id=action.id,
                    directive_id=directive.id,
                    metric=directive.metric,
                    delta=float(delta),
                    projected=projected,
                    would_violate=satisfied_now and not satisfied_after,
                    setback_turns=_setback(directive.metric, float(delta), measurements),
                    directive_priority=directive.priority,
                )
            )
    return out


def _setback(metric: str, delta: float, measurements: dict[str, float]) -> float | None:
    """How many turns of progress a delta undoes, where a rate is known.

    Returns None rather than guessing. A setback figure with an invented denominator is worse
    than no figure, because it looks like the one piece of hard arithmetic in the block.
    """
    rate_name = RATE_OF.get(metric)
    if rate_name is None:
        return None
    rate = measurements.get(rate_name)
    if not isinstance(rate, int | float) or rate == 0:
        return None
    turns = abs(delta) / abs(float(rate))
    return round(turns, 1)


def _n(value: float) -> str:
    """Integers as integers. Most of these are counts, and ``82.0 credits`` reads as noise."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _delta(delta: float) -> str:
    if delta == 0:
        return "unchanged"
    return f"{'+' if delta > 0 else ''}{_n(delta)}"


class DirectiveStore:
    """Directives in force, optionally persisted.

    Keyed by directive id so that re-issuing one replaces it rather than accumulating a second
    copy — a long-horizon decision that fires every few turns will restate a plan it still
    believes in, and two contradictory copies of the same id would be worse than either.

    Persistence is one JSON file rewritten in full. Directives are few and change rarely, so
    append-and-replay would buy nothing and cost the ability to read the file at a glance.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._by_id: dict[str, Directive] = {}
        if path is not None:
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in payload.get("directives", []):
                directive = Directive.model_validate(raw)
                self._by_id[directive.id] = directive
        except Exception:  # noqa: BLE001 — a corrupt plan file must not stop a game
            # Losing the plan is bad; refusing to play is worse. The turn continues with no
            # standing directives, which is where every game starts anyway.
            self._by_id.clear()

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"directives": [d.model_dump(mode="json") for d in self._by_id.values()]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def add(self, directives: Iterable[Directive]) -> None:
        changed = False
        for directive in directives:
            self._by_id[directive.id] = directive
            changed = True
        if changed:
            self._save()

    def in_force(self, turn: int) -> list[Directive]:
        """Directives that still apply at ``turn``, oldest first.

        Oldest first so that a model reads a plan in the order it was made, which is the order
        that explains it. Expired ones are dropped from the store as they are noticed rather
        than in a sweep — the read is where the current turn is known.
        """
        expired = [d for d in self._by_id.values() if _expired(d, turn)]
        if expired:
            self._by_id = {k: d for k, d in self._by_id.items() if not _expired(d, turn)}
            self._save()
        live = [d for d in self._by_id.values() if _active(d, turn)]
        return sorted(live, key=lambda d: (d.issued_turn or d.starts_turn or 0, d.id))

    def for_entities(self, entity_ids: Iterable[str]) -> list[Directive]:
        """Directives naming any of these datalinks entities.

        The reverse index the entity links exist for: given the project a base is considering,
        find the standing plans about it. Linear over the store because a plan is tens of entries,
        not thousands — and an index that has to be kept in sync is a bug surface bought for a
        scan that costs nothing at this size.
        """
        wanted = {e for e in entity_ids if e}
        if not wanted:
            return []
        return [d for d in self._by_id.values() if wanted & set(d.entities)]

    def revoke(self, directive_id: str) -> bool:
        if self._by_id.pop(directive_id, None) is None:
            return False
        self._save()
        return True

    def __len__(self) -> int:
        return len(self._by_id)
