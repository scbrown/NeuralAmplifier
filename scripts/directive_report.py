#!/usr/bin/env python3
"""Per-directive attention and override rates over a run — na-mmp's analysis half.

## What this answers that `just coverage` does not

`coverage` reports adherence in aggregate: how often the brain followed *something*. na-mmp
wants it **per directive**, because the two failures it names are invisible in an average:

  * *A priority-7 directive overridden on every decision was mispriced.* In aggregate it is
    diluted by every directive that was followed.
  * *One never overridden may be costing more than it admits.* Perfect adherence looks like
    success and is equally consistent with a directive that never constrained anything.

Both are properties of one directive across many decisions, so the unit of the report is the
directive, not the run.

## The distinction it refuses to blur

`unmeasurable` counts directives whose metric the world view did not report. That is an
**adapter gap** — the metric is missing from the payload — and it must not be read as a
directive that was checked and failed. Folding the two together would turn a bug in the adapter
into an apparently unhelpful directive, and the fix for those two things is in different
repositories. So unmeasurable decisions are excluded from the rates and reported separately,
loudly.

## What still needs a game

The rates need a run that plays turns; ten replays of one captured observation show the
mechanism works and say nothing about whether directives help. This reads whatever log exists,
so the moment there is a real run the analysis is already here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


class Tally:
    __slots__ = ("in_force", "followed", "overrode", "unmeasurable", "unsatisfied", "priority")

    def __init__(self) -> None:
        self.in_force = 0
        self.followed = 0
        self.overrode = 0
        self.unmeasurable = 0
        self.unsatisfied = 0
        self.priority: int | None = None

    @property
    def measurable(self) -> int:
        """Decisions where this directive was in force AND its metric was reported.

        The denominator for both rates. Using `in_force` instead would charge a directive for
        decisions it could never have been checked on.
        """
        return self.in_force - self.unmeasurable

    @property
    def attention(self) -> float | None:
        return self.followed / self.measurable if self.measurable else None

    @property
    def override(self) -> float | None:
        return self.overrode / self.measurable if self.measurable else None


def measurable_fraction(tallies: dict[str, Tally]) -> float | None:
    """Fraction of in-force directive opportunities whose metric was observable."""

    in_force = sum(t.in_force for t in tallies.values())
    if not in_force:
        return None
    return sum(max(0, t.measurable) for t in tallies.values()) / in_force


def directive_measurable_fraction(tally: Tally) -> float | None:
    """Measurability for one directive, the report's actual unit of analysis."""

    return max(0, tally.measurable) / tally.in_force if tally.in_force else None


def tally(path: Path) -> tuple[dict[str, Tally], int]:
    tallies: dict[str, Tally] = defaultdict(Tally)
    decisions = 0

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        plan = record.get("plan")
        if not isinstance(plan, dict):
            continue
        decisions += 1

        for field in ("in_force", "followed", "overrode", "unmeasurable", "unsatisfied"):
            for directive_id in plan.get(field) or []:
                if isinstance(directive_id, str):
                    setattr(tallies[directive_id], field, getattr(tallies[directive_id], field) + 1)

        # Priority comes from the world view's directive block when the log carries it; it is
        # what makes "mispriced" a statement rather than a feeling.
        for directive in record.get("directives") or []:
            if isinstance(directive, dict) and isinstance(directive.get("id"), str):
                entry = tallies[directive["id"]]
                if isinstance(directive.get("priority"), int):
                    entry.priority = directive["priority"]

    return dict(tallies), decisions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", help="decisions.jsonl from a run")
    ap.add_argument(
        "--min-decisions",
        type=int,
        default=20,
        help="refuse a log too short for a rate to mean anything",
    )
    ap.add_argument(
        "--min-measurable-fraction",
        type=float,
        metavar="FRACTION",
        help=(
            "fail unless at least this fraction of in-force directive opportunities had an "
            "observable metric (0.0 through 1.0)"
        ),
    )
    args = ap.parse_args()
    if args.min_measurable_fraction is not None and not 0 <= args.min_measurable_fraction <= 1:
        ap.error("--min-measurable-fraction must be between 0.0 and 1.0")

    path = Path(args.log)
    if not path.is_file():
        print(f"no such log: {path}", file=sys.stderr)
        return 1

    tallies, decisions = tally(path)
    if not tallies:
        print(
            "no directive was in force on any decision in this log — nothing to report.\n"
            "That is a legitimate outcome, not an error: a run with no standing plan has no\n"
            "attention to measure.",
            file=sys.stderr,
        )
        return 1

    if decisions < args.min_decisions:
        print(
            f"refusing: {decisions} decision(s) carrying a plan block, and a rate needs "
            f"{args.min_decisions}.\n"
            "Ten replays of one captured observation show the mechanism works and say nothing\n"
            "about whether directives help — which is na-mmp's entire point.",
            file=sys.stderr,
        )
        return 1

    print(f"{decisions} decisions with a plan block\n")
    header = (
        f"{'directive':<22} {'pri':>3} {'in force':>9} {'attn':>6} {'override':>9} {'unmeas':>7}"
    )
    print(header)
    print("-" * len(header))

    adapter_gap = 0
    for directive_id, t in sorted(tallies.items()):
        attn = f"{t.attention:.2f}" if t.attention is not None else "  n/a"
        over = f"{t.override:.2f}" if t.override is not None else "  n/a"
        pri = str(t.priority) if t.priority is not None else "?"
        print(
            f"{directive_id:<22} {pri:>3} {t.in_force:>9} {attn:>6} {over:>9} {t.unmeasurable:>7}"
        )
        adapter_gap += t.unmeasurable

    print()
    # The two failures na-mmp names, called out rather than left in the numbers.
    for directive_id, t in sorted(tallies.items()):
        if t.measurable < 5:
            continue
        if t.override == 1.0 and (t.priority or 0) >= 6:
            print(
                f"MISPRICED?  {directive_id} is priority {t.priority} and was overridden on "
                f"every one of {t.measurable} measurable decisions."
            )
        if t.override == 0.0 and t.measurable >= 10:
            print(
                f"UNTESTED?   {directive_id} was never overridden in {t.measurable} decisions — "
                "consistent with a directive that helps, and equally with one that never "
                "constrained anything."
            )

    if adapter_gap:
        print(
            f"\nADAPTER GAP: {adapter_gap} decision-directive pair(s) were unmeasurable — the\n"
            "world view did not report the metric. That is a missing field in the adapter, NOT\n"
            "a directive that failed, and it is excluded from the rates above. Fixing it is a\n"
            "change in the adapter repository, not in the plan."
        )
    if args.min_measurable_fraction is not None:
        failing = [
            (directive_id, directive_measurable_fraction(t))
            for directive_id, t in sorted(tallies.items())
            if directive_measurable_fraction(t) is None
            or directive_measurable_fraction(t) < args.min_measurable_fraction
        ]
        if failing:
            rendered = ", ".join(
                f"{directive_id}={'unanswerable' if observed is None else f'{observed:.3f}'}"
                for directive_id, observed in failing
            )
            print(
                f"\nMEASURABILITY CONTRACT FAIL: {rendered}; every directive requires "
                f">= {args.min_measurable_fraction:.3f}.",
                file=sys.stderr,
            )
            return 2
        observed = measurable_fraction(tallies)
        assert observed is not None
        print(
            f"\nMEASURABILITY CONTRACT PASS: all {len(tallies)} directive(s) >= "
            f"{args.min_measurable_fraction:.3f}; aggregate {observed:.3f}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
