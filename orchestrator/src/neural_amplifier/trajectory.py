"""What a metric has been DOING, not only what it is — the horizon a decision cannot see.

`WorldView.metrics` carries this turn's thirteen numbers. A decision reading it knows the
faction has 185 energy credits; it cannot know whether that is 185 on the way up from 40 or 185
on the way down from 673. Those are opposite situations and the same world view.

That gap is the diagnosis `na-6db` reached from the other end. It measured the brain's
`base.hurry` overrides against the deterministic tier's and found them reliably worse, and wrote:

    Thinker declining a hurry is not conservatism. It is the correct call about compounding, and
    the brain has no representation of that anywhere in its world view.

Compounding is a quantity that only exists ACROSS turns. Nothing in the contract carried one, so
the brain was asked to weigh a cost it had no way to see, on 699 decisions, and got it wrong in
the direction that spends.

**Nothing here needs the adapter.** The orchestrator already receives every world view and every
outcome; `turns.py` already folds them into a per-turn view. A series is a rearrangement of
records the service is holding anyway — no new measurement, no new contract field from the
engine, and no invariant-2 question about the orchestrator learning where an engine files its
economy. These are the adapter's own numbers, kept.

Two rules, both inherited from mechanisms that already got this right:

**An absent metric is a GAP, never a zero.** `metrics.py` has the `accumulated` flag for exactly
this — `energy_income` is genuinely unreadable inside the production phase, so every base-scope
world view omits it. A series that filled the hole with 0 would manufacture a collapse out of an
adapter's honest silence, and it would look like the single most alarming line in the block.

**A short series says so.** At turn 4 there is no `t-20`. The offset is simply absent from `at`.
It is not `null` and it is not `0`: both of those are values a reader can do arithmetic on, and
the whole point of this module is that arithmetic on a fabricated history is worse than no
history. This is `directives.py`'s unmeasurable rule one level down — silence must not flatter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from .metrics import VOCABULARY

#: How far back a series looks, in turns. Three points and a slope, not a full history: the block
#: rides in every prompt, and a decision needs to know the DIRECTION and roughly how fast, not to
#: re-derive the game. Chosen to straddle the horizons the surfaces actually work on — a hurry
#: saves ~5 turns, an expansion checkpoint runs ~20.
OFFSETS: tuple[int, ...] = (5, 10, 20)


@dataclass(frozen=True)
class Series:
    """One metric's recent past, as offsets that were actually observed.

    `at` is keyed `"t-5"`, `"t-10"`, `"t-20"` and **contains only offsets a real observation was
    found for**. A caller must not read a missing key as anything but missing.
    """

    now: float
    at: dict[str, float]
    #: Change per turn across the widest span the series actually covers, or ``None`` when there
    #: is only one point. ``None`` rather than ``0.0`` on purpose: a flat metric and an unknown
    #: one are different, and 0.0 is the reading a decision would most readily act on.
    slope_per_turn: float | None = None

    def wire(self) -> dict[str, float | None]:
        """The shape that reaches the model. Absent offsets stay absent."""
        out: dict[str, float | None] = {"now": self.now}
        out.update(self.at)
        if self.slope_per_turn is not None:
            out["slope_per_turn"] = round(self.slope_per_turn, 2)
        return out


def _nearest_at_or_before(
    points: list[tuple[int, float]], target: int, *, not_before: int | None = None
) -> tuple[int, float] | None:
    """The most recent observation at or before `target`, and no older than `not_before`.

    At-or-before rather than exact: the engine does not raise a decision on every turn for every
    surface, so demanding turn 121 exactly would empty the block on precisely the low-frequency
    surfaces (`faction.tech`, `faction.se`) that most need a horizon. Taking the nearest earlier
    point answers "what was it around then", which is the question.

    **`not_before` is what keeps the LABEL honest, and it was missing at first.** Without it a
    turn-104 observation happily answered for `t-5` on a turn-126 decision — a value twenty-two
    turns old, presented to the model under a five-turns-ago heading. That is precisely the
    fabrication the rest of this module refuses, arriving through the one door left open: not an
    invented number, but a real number under a wrong name, which is harder to notice and just as
    wrong to reason from. Found by a test whose expectation was wrong for the right reason.

    It deliberately never looks FORWARD. A later observation is not history, and reaching for one
    would let a series describe a turn that had not happened when the decision was made.
    """
    found = None
    for turn, value in points:
        if turn > target:
            continue
        if not_before is not None and turn < not_before:
            continue
        if found is None or turn > found[0]:
            found = (turn, value)
    return found


def derive(
    observations: Iterable[tuple[int, Mapping[str, float]]],
    turn: int,
    *,
    offsets: tuple[int, ...] = OFFSETS,
    scope: Literal["faction", "base"] | None = "faction",
) -> dict[str, dict[str, float | None]]:
    """Build the trajectory block for a decision at `turn`.

    `observations` is any iterable of `(turn, metrics)` the orchestrator already holds — decision
    records, world views, the turn store. Order does not matter and duplicates are fine; the most
    recent observation at or before each point wins.

    A metric absent from the CURRENT turn gets no series at all. Reporting a history for something
    the world view does not currently report would put a number in front of the model with no
    present value to compare it against, which is the shape of an invented fact.

    **`scope` defaults to faction, and the default is the safe one.** A record stream is a mix:
    base-scope metrics (`pop_size`, `mineral_surplus`, `minerals_remaining`,
    `turns_to_completion`) arrive from whichever base the engine happened to ask about, so
    stringing them together across turns produces a series about a DIFFERENT BASE at each point.
    Measured on the na-6db stream: `pop_size` came out as `now 4, t-5 4, t-10 3, slope 0.1` — a
    tidy-looking growth curve assembled from three unrelated bases.

    That is the worst failure this module can have. Every other rule here refuses to say
    something it cannot support; this one would state a well-formed, plausible, entirely
    meaningless number, and the slope would make it look considered. Filtering on
    `metrics.VOCABULARY`'s declared scope is the fix, and it uses the scope metadata the way
    `pool` is used in `metrics.py` — a property that lives beside the metric rather than a list
    of special cases in whichever module noticed.

    Pass `scope="base"` only for a stream you know is one base's, and `scope=None` to disable the
    filter entirely (a metric outside the vocabulary is then kept, which is why this is opt-in).
    """
    points: dict[str, list[tuple[int, float]]] = {}
    for observed_turn, metrics in observations:
        if not isinstance(observed_turn, int):
            continue
        for name, value in (metrics or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if scope is not None:
                metric = VOCABULARY.get(name)
                # Unknown names are dropped under a scope filter: the filter's promise is that
                # every series is about one subject, and a name the vocabulary does not describe
                # cannot be checked against it.
                if metric is None or metric.scope != scope:
                    continue
            points.setdefault(name, []).append((observed_turn, float(value)))

    out: dict[str, dict[str, float | None]] = {}
    for name, series in points.items():
        current = _nearest_at_or_before(series, turn)
        # Only a metric observed AT this turn gets a block — `_nearest_at_or_before` would
        # happily hand back a value from thirty turns ago and call it `now`.
        if current is None or current[0] != turn:
            continue

        at: dict[str, float] = {}
        widest: tuple[int, float] | None = None
        for offset in offsets:
            # No older than TWICE the offset. A `t-5` answered by a turn-20-turns-ago
            # observation is a lie with a schema behind it; omitting it is the honest state
            # and the one every other rule here already takes.
            past = _nearest_at_or_before(series, turn - offset, not_before=turn - 2 * offset)
            if past is None or past[0] == turn:
                # `past[0] == turn` means nothing older exists and the lookup fell through to the
                # current point. A series whose past is its present is not a series.
                continue
            at[f"t-{offset}"] = past[1]
            if widest is None or past[0] < widest[0]:
                widest = past

        slope = None
        if widest is not None and turn != widest[0]:
            slope = (current[1] - widest[1]) / (turn - widest[0])

        out[name] = Series(now=current[1], at=at, slope_per_turn=slope).wire()

    return out
