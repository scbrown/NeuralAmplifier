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


def evaluate(directives: Sequence[Directive], world_view: WorldView) -> list[DirectiveStatus]:
    """Measure each directive against this world view.

    Expired directives are excluded — a plan whose horizon has passed is history, and leaving it
    in front of a model as though it still applied would be actively misleading.
    """
    measurements = world_view.metrics or {}
    statuses: list[DirectiveStatus] = []
    for directive in directives:
        if _expired(directive, world_view.turn):
            continue
        statuses.append(_status(directive, measurements))
    return statuses


def _expired(directive: Directive, turn: int) -> bool:
    return directive.horizon_turn is not None and turn > directive.horizon_turn


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
        live = [d for d in self._by_id.values() if not _expired(d, turn)]
        if len(live) != len(self._by_id):
            self._by_id = {d.id: d for d in live}
            self._save()
        return sorted(live, key=lambda d: (d.issued_turn or 0, d.id))

    def revoke(self, directive_id: str) -> bool:
        if self._by_id.pop(directive_id, None) is None:
            return False
        self._save()
        return True

    def __len__(self) -> int:
        return len(self._by_id)
