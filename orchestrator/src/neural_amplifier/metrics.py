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
honest order of work is: emit it from the adapter first, add the name second. That promise is
enforced — ``tests/test_metrics_vocabulary.py`` holds the set the Thinker adapter emits and
fails if the two drift, because an unkept promise here is invisible at runtime: the directive
is accepted, then reports UNMEASURABLE forever.
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


def _m(name: str, scope: MetricScope, unit: str, better: Better, description: str) -> Metric:
    return Metric(name=name, scope=scope, unit=unit, better=better, description=description)


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
        ),
        _m(
            "energy_income",
            "faction",
            "credits/turn",
            "higher",
            "Net energy credits added per turn. Reserves without income is a stock with no "
            "flow, and the two justify completely different spending.",
        ),
        _m(
            "labs_output",
            "faction",
            "points/turn",
            "higher",
            "Research points produced per turn.",
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
        # --- base economy.
        _m(
            "mineral_surplus",
            "base",
            "minerals/turn",
            "higher",
            "Net minerals produced by this base per turn.",
        ),
        _m(
            "minerals_remaining",
            "base",
            "minerals",
            "lower",
            "Minerals still needed to finish what this base is building.",
        ),
        # `turns_to_completion` lived here and was removed (na-c17). No adapter ever emitted
        # it, and the Thinker adapter declines to on purpose: a partially built item makes
        # that arithmetic subtly wrong, and it ships accumulated and surplus separately so
        # nothing has to guess. It is also derivable — ``RATE_OF`` turns minerals_remaining
        # into turns via mineral_surplus already. A name nothing reports is not a harmless
        # placeholder: it is a directive that can be written and can never be checked.
        _m("pop_size", "base", "citizens", "higher", "Population of this base."),
    )
}


def known(name: str) -> bool:
    """Whether ``name`` is a metric a directive may reference."""
    return name in VOCABULARY


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
