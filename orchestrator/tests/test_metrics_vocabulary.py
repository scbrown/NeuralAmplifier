"""The vocabulary is a promise that an adapter reports the name. This is the promise.

`metrics.py` holds names; `directives.py` evaluates every standing plan against
`WorldView.metrics`. Nothing in between checks that an adapter ever *fills* that block, and the
failure when it doesn't is silent in the worst way: the directive is accepted at issue time,
looks live in the record's `in_force`, and comes back `unmeasurable` on every decision for the
rest of the game. Unmeasurable is not unsatisfied — it means nobody ever checked — so a plan
that steers nothing is indistinguishable from one being followed unless something asserts the
seam.

What this caught: the Thinker adapter emitted its six faction measurements under a
`faction_state` key while the orchestrator read `metrics`. Both sides were internally
consistent, both were tested, and the chain between them was dead — every directive on a real
world view was unmeasurable regardless of what the adapter reported.
"""

from __future__ import annotations

import json
from pathlib import Path

from neural_amplifier.contract import WorldView
from neural_amplifier.directives import evaluate
from neural_amplifier.metrics import VOCABULARY

#: What `na_write_metrics` in the Thinker adapter (`scbrown/thinker`, `src/neural.cpp`) writes
#: into `WorldView.metrics`. Split the way the adapter splits it: the faction half goes on every
#: surface, the base half only on base-scope ones.
#:
#: Hand-maintained, and deliberately so — the adapter is a different repository in a different
#: language, so this is the seam written down rather than inferred. Changing the adapter without
#: changing this is the mistake; that is why the assertion below is against the whole vocabulary
#: rather than a subset.
THINKER_FACTION = {
    "energy_reserves",
    "energy_income",
    "labs_output",
    "base_count",
    "pop_total",
    "military_units",
    "drone_total",
}
THINKER_BASE = {"pop_size", "mineral_surplus", "minerals_remaining"}


def test_every_name_in_the_vocabulary_is_one_an_adapter_emits() -> None:
    """na-c17's acceptance criterion, as a test rather than a promise.

    A name here that no adapter reports produces directives that are permanently unmeasurable,
    which is the exact failure the closed vocabulary exists to prevent — so the vocabulary must
    not grow ahead of the adapter. If this fails, the fix is to emit the metric, or to drop the
    name; adding it to the sets above without touching `neural.cpp` is how the guarantee dies.
    """
    assert THINKER_FACTION | THINKER_BASE == set(VOCABULARY)


def test_the_scopes_agree_with_the_vocabulary() -> None:
    """A faction-scope name emitted only on base surfaces would be unmeasurable on exactly the
    long-horizon decisions directives are for."""
    assert {n for n in VOCABULARY if VOCABULARY[n].scope == "faction"} == THINKER_FACTION
    assert {n for n in VOCABULARY if VOCABULARY[n].scope == "base"} == THINKER_BASE


def _fixture() -> WorldView:
    path = Path(__file__).parent / "fixtures" / "thinker_base_production.json"
    return WorldView.model_validate(json.loads(path.read_text()))


def test_a_thinker_world_view_reports_the_whole_vocabulary() -> None:
    """The fixture is the contract seam (`docs/building-and-testing.md` §5). A base-scope
    Thinker world view reports both halves, so any directive is checkable on it."""
    reported = set(_fixture().metrics or {})
    assert reported == THINKER_FACTION | THINKER_BASE


def test_directives_on_a_real_world_view_are_measurable() -> None:
    """End to end, and the assertion that actually fails when the seam breaks.

    `satisfied is None` is the unmeasurable state. Before the adapter wrote `metrics`, every
    one of these came back None — which the record reports as a gap in the *adapter*, and which
    nothing in the orchestrator's own test suite could see.
    """
    from neural_amplifier.contract import Directive

    plan = [
        Directive(
            id=f"hold-{name}",
            intent=f"A plan written against {name}.",
            metric=name,
            comparator="at_least",
            target=1.0,
        )
        for name in sorted(VOCABULARY)
    ]
    statuses = evaluate(plan, _fixture())

    unmeasurable = [s.directive.metric for s in statuses if s.satisfied is None]
    assert unmeasurable == []
