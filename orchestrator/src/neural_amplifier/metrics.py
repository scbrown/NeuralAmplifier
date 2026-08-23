"""The metric vocabulary — the names a directive is allowed to talk about.

This module holds **no values**. Values arrive from the adapter in ``WorldView.metrics``, an
engine-neutral ``{name: number}`` block, because the orchestrator must not learn where a
particular engine files its economy (``AGENTS.md`` invariant 2). What lives here is the
*vocabulary*: which names exist, what each one means, and which way is better.

Why a closed vocabulary at all, rather than letting a directive name anything:

A directive is only worth having if it can be checked. "Keep reserves above 100" is a claim
about the world that a later turn can confirm or refute; "play aggressively" is not. The
difference is entirely whether the thing named is measured. So the vocabulary is the enforcement
point — a directive that names something outside it is rejected at issue time, when the model
that wrote it is still in the loop, rather than silently ignored on every decision afterwards.

The second half of that discipline lives in ``directives.py``: a metric that is *in* the
vocabulary but *absent* from a given world view yields an explicitly unmeasurable status. A
missing measurement must never read as a satisfied one — that is the failure mode that would let
the whole mechanism drift into decoration.

Adding a name here is cheap and safe. It is a promise that some adapter reports it, so the
honest order of work is: emit it from the adapter first, add the name second.

That promise is now *enforced*, because it had already been broken once. A name nothing reports
is worse than a missing one: a directive written against it is accepted at issue time and
evaluates UNMEASURABLE forever, which in a record reads as compliance rather than as a gap. An
agent can issue directives directly now, so an aspirational name is a trap with a user-facing
path to it.

Two tests hold the line, from opposite ends. ``test_metrics_vocabulary.py`` compares this set
against what the Thinker adapter emits and fails if the two drift — the promise checked as a
set. ``test_adapter_contract.py`` checks it against evidence: every faction-scope name here has
to appear in the metrics block of every pinned adapter record, and every base-scope name in
every base-scope record. The first catches a name added ahead of its adapter; the second
catches a name the adapter stopped reporting.

**The metadata is a contract, not a comment.** ``scope`` and ``better`` are read by code, and
na-co2 is what happens when a consumer reads *more* out of them than they say. ``StateGuard``
inferred from "lower is better" that ``minerals_remaining`` was a budget an order could
overdraw. It is a **shortfall** — minerals still owed — so every build option cost more than it
by construction, and the guard denied the entire action space of every base mid-build, most
sharply the one choice the project most wants right (continuing a nearly-finished item). The
fix is the ``pool`` flag below: the thing the guard needs is now *declared* here, next to the
metric it is about, rather than deduced in ``hank.py`` from a direction that never meant it.

Which is the shape any future consumer of this metadata should copy. A property a metric either
has or has not is a field here; a list of special-cased names living in the module that happens
to care is how the same bug comes back under a different name.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

#: Which direction counts as improvement. ``None`` for quantities where neither direction is
#: inherently good — base count is better higher, drone count better lower, but "turns until
#: production completes" depends entirely on what is being built.
Better = Literal["higher", "lower"] | None

#: What a metric is attached to. A faction-scope directive evaluated against a base-scope
#: measurement is comparing different things, and saying so beats quietly comparing them.
MetricScope = Literal["faction", "base"]


@dataclass(frozen=True)
class Metric:
    """One measurable quantity a directive may reference."""

    name: str
    scope: MetricScope
    unit: str
    better: Better
    description: str
    #: Whether the value reported for this name is a **pool**: a stock of something spendable,
    #: where the number *is* the amount available and zero is a floor the game enforces. Only
    #: then does ``current + delta < 0`` describe a move the state cannot support, rather than
    #: arithmetic that merely happens to go negative.
    #:
    #: Nothing else in this dataclass implies it, which is the point (na-co2). ``better`` is
    #: about which way to want the number to move; ``unit`` is what it is counted in. Neither
    #: says whether the number is a balance you draw down — ``energy_reserves`` and
    #: ``minerals_remaining`` are both quantities of a resource, and one is a bank while the
    #: other is a debt.
    #:
    #: Default ``False`` on purpose, and the default is the safe one. A pool nobody flagged
    #: costs an affordability check that never runs — the guard is silent, which is what it
    #: already does for any metric the world view omits. A non-pool flagged by mistake denies
    #: legal moves. So this is opt-in, and adding one is a deliberate edit with
    #: ``test_metrics_vocabulary.py`` asking you to mean it.
    pool: bool = False
    #: Whether this value is a RUNNING TOTAL the engine re-accumulates during the production
    #: phase — and therefore **absent from any world view built inside that window**, which is
    #: every base-scope world view.
    #:
    #: `scope` says which thing the number is about; this says when it can be read at all, and
    #: the two are independent. `energy_income` is faction-scope AND unavailable on a base
    #: decision: `mod_production_phase` zeroes the totals and re-sums them one base at a time,
    #: `mod_base_build` fires inside that window, so the adapter would be reporting a partial
    #: sum. It omits the key instead (na-an6, thinker `neural.cpp`), which is right — 0 is a
    #: value this vocabulary genuinely admits, so a half-summed income would be indistinguishable
    #: from a faction that really earns nothing.
    #:
    #: Declared here rather than special-cased in whichever test noticed, for the reason `pool`
    #: is: a property a metric either has or has not belongs beside the metric. Measured on a
    #: live game (na-095): `energy_income` and `labs_output` appear on 3 of 3 `faction.se`
    #: records and **0 of 169** base-scope ones, while every non-accumulated faction name
    #: appears on all 172.
    #:
    #: A directive written against one of these is not broken — it is simply unmeasurable on a
    #: base decision, which is the honest state and what `directives.py` already reports. The
    #: value of saying so here is that "unmeasurable" stops looking like a bug.
    accumulated: bool = False


def _m(
    name: str,
    scope: MetricScope,
    unit: str,
    better: Better,
    description: str,
    *,
    pool: bool = False,
    accumulated: bool = False,
) -> Metric:
    return Metric(
        name=name,
        scope=scope,
        unit=unit,
        better=better,
        description=description,
        pool=pool,
        accumulated=accumulated,
    )


#: The vocabulary. Deliberately small: every name is one an adapter actually emits, so a
#: directive written against it can be evaluated today. An aspirational name is worse than a
#: missing one, because it produces directives that are permanently unmeasurable.
VOCABULARY: Final[dict[str, Metric]] = {
    m.name: m
    for m in (
        # --- faction economy. The gap that na-56o identified: base.hurry asks whether 81
        # credits is worth seven turns, and nothing in the world view said what else that
        # energy could buy or whether it would be replaced.
        _m(
            "energy_reserves",
            "faction",
            "credits",
            "higher",
            "Energy credits banked and spendable now.",
            # The one pool in the vocabulary today, and the only name an affordability check
            # means anything against: the number is the balance, spending draws it down, and
            # the engine will not let it go below zero. base.hurry is the surface that spends
            # it, which is why that surface is the one StateGuard was built for.
            pool=True,
        ),
        _m(
            "energy_income",
            "faction",
            "credits/turn",
            "higher",
            "Net energy credits added per turn. Reserves without income is a stock with no "
            "flow, and the two justify completely different spending. Absent from base-scope "
            "world views — see `accumulated`.",
            accumulated=True,
        ),
        _m(
            "labs_output",
            "faction",
            "points/turn",
            "higher",
            "Research points produced per turn. Absent from base-scope world views — see "
            "`accumulated`.",
            accumulated=True,
        ),
        _m(
            "base_count",
            "faction",
            "bases",
            "higher",
            "Bases owned. The most direct measure of expansion, which is what most "
            "long-horizon plans in this game are ultimately about.",
        ),
        _m("pop_total", "faction", "citizens", "higher", "Total population across all bases."),
        _m(
            "military_units",
            "faction",
            "units",
            None,
            "Units with a non-zero attack or defence value. Neither direction is inherently "
            "good — it trades against everything else the minerals could have been.",
        ),
        _m(
            "drone_total",
            "faction",
            "citizens",
            "lower",
            "Superdrones and drones across all bases; the constraint that usually binds an "
            "SE choice.",
        ),
        # The first name here that exists for a *diplomatic* commitment rather than an economic
        # one, and the reason is na-nmg: a diplomacy answer is a promise about later turns, and
        # a promise nothing can measure is one the game never notices was broken. Miriam asks
        # the Peacekeepers to withdraw; "at once" is accepted, the troops stay, and the treaty
        # breaks without anyone having decided to break it. A directive needs a name to be
        # written against, and this is that name.
        # The war-state name na-7bk's design comment asks for first and na-tit's adapter work
        # made emittable. Both mechanisms that need it — conditional queued answers and
        # unit-intent review triggers — refuse a predicate they cannot measure, so until the
        # adapter reported this the design's own headline example ("holds unless at_war flips")
        # could not be written at all.
        _m(
            "factions_at_war",
            "faction",
            "factions",
            # None, and deliberately not "lower". Fewer fronts is safer and more of them is
            # sometimes the plan; the trigger this exists for fires on the number MOVING, not
            # on which way is good. `military_units` is None for the same reason.
            None,
            "Factions we are at war with, counted only among those we have met. A count "
            "rather than a flag: the comparators are numeric, so a boolean could say 'at war' "
            "but not 'a second front opened', and the latter is the change a standing answer "
            "needs to stop on. Zero is peace with no special case.",
        ),
        _m(
            "units_in_foreign_territory",
            "faction",
            "units",
            "lower",
            "Own units standing on another faction's tiles, counting only factions we have "
            "met. Position, not intent — a unit already marching home still counts until it "
            "is out, which is what lets a withdrawal promise be seen to be kept.",
        ),
        # --- base economy.
        _m(
            "mineral_surplus",
            "base",
            "minerals/turn",
            "higher",
            "Net minerals produced by this base per turn.",
        ),
        # Emphatically NOT a pool, and the metric that taught us the field has to exist. This
        # is a debt, not a balance: it is what the base still owes on its current item, so
        # "spending" it is meaningless and it is *smaller* the better the base is doing. A
        # guard that read it as a budget denied every option on every base mid-build, because
        # the shortfall is by construction no larger than the item already being paid for
        # (na-co2, measured turn 42: banked 27 of 33, shortfall 6, and all nine options
        # denied).
        _m(
            "minerals_remaining",
            "base",
            "minerals",
            "lower",
            "Minerals still needed to finish what this base is building.",
        ),
        # Removed once (na-c17) on the grounds that no adapter emitted it and the arithmetic is
        # subtly wrong on a partially built item. The first half stopped being true and the
        # second was answered rather than argued with: the Thinker adapter emits this, and
        # omits the key entirely when mineral_surplus <= 0 rather than emitting a zero that
        # would read as "completes this turn" — the opposite of the truth. So it is reported,
        # conditionally and honestly, and a directive written against it can be checked.
        #
        # ``better`` is None deliberately. Fewer turns is not inherently better: it is better
        # for a defender wanted now and worse for a secret project deliberately paced.
        _m(
            "turns_to_completion",
            "base",
            "turns",
            None,
            "Turns until this base finishes what it is building, at the current mineral"
            " surplus. Absent when the base produces no surplus and would never finish.",
        ),
        _m("pop_size", "base", "citizens", "higher", "Population of this base."),
    )
}


def known(name: str) -> bool:
    """Whether ``name`` is a metric a directive may reference."""
    return name in VOCABULARY


def is_pool(name: str) -> bool:
    """Whether ``name`` reports a spendable pool — see :attr:`Metric.pool`.

    The question ``StateGuard`` has to ask before deciding an order is unaffordable, asked of
    the vocabulary rather than answered from the metric's own name or direction.

    An unknown name is not a pool. Effects are declared by the adapter and the adapter can name
    anything (``faction.se`` declares its social-engineering deltas under the engine's own
    effect names, which are not measurements at all), so the closed vocabulary is the only thing
    standing between an arbitrary key and an affordability denial.
    """
    metric = VOCABULARY.get(name)
    return metric is not None and metric.pool


def describe(name: str) -> str:
    """A one-line gloss, for putting in front of a model that must use the name correctly."""
    metric = VOCABULARY.get(name)
    if metric is None:
        return f"{name} (unknown metric)"
    direction = {"higher": "higher is better", "lower": "lower is better", None: "no direction"}[
        metric.better
    ]
    return f"{name} [{metric.unit}, {metric.scope}-scope, {direction}] — {metric.description}"


def vocabulary_prompt() -> str:
    """The vocabulary rendered for a system prompt.

    A model asked to issue a measurable directive needs to know what is measurable, and the only
    way it can know is if we tell it. Guessing a plausible metric name is exactly the mistake the
    closed vocabulary exists to catch, and catching it after the fact is a worse experience than
    preventing it.
    """
    lines = ["Metrics you may write a directive against:"]
    lines.extend(f"  - {describe(name)}" for name in sorted(VOCABULARY))
    return "\n".join(lines)
