#!/usr/bin/env python3
"""Does a decision's reasoning survive the turn that produced it? — aegis-n8zmq's baseline half.

## What this answers that nothing else does

`directive_report.py` asks whether a standing plan was *read*. `ab_outcomes.py` asks whether one
run's trajectory beat another's. Neither asks the question Stiwi's redesign turns on: **how much
of a game's play is reasoning that outlived the turn it was produced in, and how much is the
same question re-answered from scratch every turn.**

Three views, over records that already exist:

  * **record-tier carry rate** — the fraction of decisions answered by queued/deferred reasoning
    a *previous* turn produced.
  * **directive opportunity carry rate** — among later decisions where an agent-issued directive
    was explicitly in force, the fraction that followed it or explicitly revised it.
  * **rework rate** — how often the same base and surface reversed its answer turn over turn.

All are computable from committed logs with no game, no model and no network, which is the
point: the design in `docs/long-horizon-play.md` has to be measured against what we do today,
not announced against a remembered impression of it.

## Which tiers count as carry, and why `plan` does not

The obvious reading — "anything that isn't `llm`" — is wrong in both directions, so the mapping
is spelled out rather than inferred from the tier name:

  queued         CARRIES. A queued answer stands across turns and names, in the measured metric
                 vocabulary, what would make it wrong. That is reasoning outliving its turn.
  deferred       CARRIES. Parked on one turn, swept on a later one.
  plan           DOES NOT CARRY. `turnplan.py` is explicit that a plan table is valid for
                 exactly the turn it names and dies with it — it is *bulk* reasoning, not
                 *long-horizon* reasoning. Counting it would let a fast agent look like a
                 strategic one, which is the exact confusion this report exists to prevent.
  llm            Turn-local by construction: the brain was asked, now, about this.
  deterministic  Turn-local, and the brain was not asked at all.

A directive followed is the other carrier, and it is counted separately because its age is
different in kind: a directive issued at turn 30 and followed at turn 130 is a hundred turns of
carry from one act of reasoning, and collapsing that into the tier histogram would hide it.

## What it refuses to report

The two log shapes in this repository carry different fields, and a rate computed over a field
that is absent is a zero that reads as a measurement — the failure this codebase keeps meeting.
So each rate is reported only when its inputs are present, and named as UNANSWERABLE when they
are not:

  rework          needs `base_id`. Adapter observation logs have it; orchestrator decision
                  records do not.
  directive age   needs `issued_turn` on the directive block. A log carrying only the plan
                  block's ids can say a directive was followed and cannot say how old it was.
  opportunities   need a run identity, turns, and `plan.in_force`. Missing any makes the funnel
                  UNANSWERABLE rather than turning an instrument gap into zero carry.

The committed strategic-review proof is a published summary rather than JSONL. This reader accepts
that shape too, because a positive control that the report cannot read is not a control.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

#: Tiers whose answer was produced by reasoning from an EARLIER turn. See the module docstring
#: for why `plan` is deliberately absent.
CARRYING_TIERS: frozenset[str] = frozenset({"queued", "deferred"})

#: Reasoning done in bulk for one turn. Real work, and not carry.
SAME_TURN_BULK: frozenset[str] = frozenset({"plan"})


@dataclass
class DirectiveFunnel:
    """Cross-record lineage for agent-issued directives.

    One opportunity is one previously-issued directive explicitly present in a later record's
    ``plan.in_force``. That field is the applicability decision made by the relevance walk; using
    every decision as the denominator would count occasions where the directive was never shown.
    """

    reviews_attempted: int = 0
    reviews_clean: int = 0
    directives_issued: int = 0
    opportunities: int = 0
    followed: int = 0
    revised: int = 0
    overrode: int = 0
    unreferenced: int = 0
    carried_turns: list[int] = field(default_factory=list)
    eligibility_errors: list[str] = field(default_factory=list)
    expiry_observable: bool = False
    expired: int = 0
    issued_at: dict[str, int] = field(default_factory=dict)
    run_identity: str | None = None

    @property
    def successes(self) -> int:
        return self.followed + self.revised

    @property
    def opportunity_rate(self) -> float | None:
        if self.eligibility_errors or not self.opportunities:
            return None
        return self.successes / self.opportunities


@dataclass
class Report:
    path: Path
    decisions: int = 0
    tiers: Counter[str] = field(default_factory=Counter)
    surfaces: Counter[str] = field(default_factory=Counter)
    turns: list[int] = field(default_factory=list)
    #: (base_id, surface, tier) -> [(turn, answer)], for the rework pass. Tier is part of the
    #: key so the deterministic tier's own churn can be read as the control it is: how often an
    #: answer *should* change on this board is not zero, and a brain's rate means nothing
    #: without it.
    answers: dict[tuple[object, str, str], list[tuple[int, object]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    has_base_id: bool = False
    #: Records with a base_id whose log line states no answer at all — excluded from rework, and
    #: reported, because a rate over a third of a log is not a rate over the log.
    no_answer: int = 0
    plan_records: int = 0
    followed_records: int = 0
    issued_by_agent: int = 0
    #: Ages in turns of directives that were followed, where the log states `issued_turn`.
    directive_ages: list[int] = field(default_factory=list)
    #: Directive ids followed whose issue turn the log does not state.
    ages_unknown: int = 0
    funnel: DirectiveFunnel = field(default_factory=DirectiveFunnel)
    published_summary: bool = False

    @property
    def carried(self) -> int:
        return sum(n for tier, n in self.tiers.items() if tier in CARRYING_TIERS)

    @property
    def bulk(self) -> int:
        return sum(n for tier, n in self.tiers.items() if tier in SAME_TURN_BULK)


def _answer(record: dict[str, object]) -> object:
    """What this record decided, in whatever shape its log writes it.

    Three shapes, and the third is why this is a function rather than one key lookup:

      applied_item        adapter observation of a decision that was APPLIED.
      chosen[0].action_id orchestrator decision record.
      native_choice_item  adapter observation of a surface the brain does not drive. The engine
                          still chose something, and its churn is the control the brain's is read
                          against — dropping these would throw away the only baseline in the
                          repository for how often an answer *should* change.

    `None` for a record with none of them keeps it out of the rework pass rather than making
    every such record look like a reversal of the last one. The count of those is reported, so
    an excluded majority cannot masquerade as a low rework rate.
    """
    for key in ("applied_item", "native_choice_item"):
        if key in record:
            return record.get(key)
    chosen = record.get("chosen")
    if isinstance(chosen, list) and chosen:
        first = chosen[0]
        if isinstance(first, dict):
            return first.get("action_id")
    return None


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _run_identity(record: dict[str, object]) -> str | None:
    for key in ("run_id", "game_id", "run"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _error_once(funnel: DirectiveFunnel, reason: str) -> None:
    if reason not in funnel.eligibility_errors:
        funnel.eligibility_errors.append(reason)


def _observe_funnel(report: Report, record: dict[str, object]) -> None:
    """Fold one chronological record into the directive opportunity funnel."""

    funnel = report.funnel
    plan = record.get("plan")
    tier = record.get("tier")
    if tier == "review":
        funnel.reviews_attempted += 1
        rejected = plan.get("rejected") if isinstance(plan, dict) else None
        if "degraded" not in record:
            _error_once(funnel, "review degradation status missing")
        if isinstance(plan, dict) and "rejected" not in plan:
            _error_once(funnel, "review rejection status missing")
        if record.get("degraded") is False and isinstance(rejected, list) and not rejected:
            funnel.reviews_clean += 1

    if not isinstance(plan, dict):
        if tier == "review":
            _error_once(funnel, "review record has no plan block")
        return

    issued = _strings(plan.get("issued"))
    followed = set(_strings(plan.get("followed")))
    overrode = set(_strings(plan.get("overrode")))
    turn = record.get("turn")
    identity = _run_identity(record)
    relevant = bool(issued or funnel.issued_at)

    if relevant:
        if identity is None:
            _error_once(funnel, "run identity missing (need run_id, game_id, or summary run)")
        elif funnel.run_identity is None:
            funnel.run_identity = identity
        elif identity != funnel.run_identity:
            _error_once(funnel, "more than one run identity appears in one report")

    if funnel.issued_at:
        if "in_force" not in plan:
            _error_once(funnel, "applicability missing (plan.in_force absent)")
        elif not isinstance(turn, int):
            _error_once(funnel, "decision turn missing for a possible later opportunity")
        else:
            # One directive-decision pair is one opportunity. A malformed or legacy record may
            # repeat an id; letting multiplicity inflate both numerator and denominator would
            # make a serialization defect look like more strategic play.
            for directive_id in dict.fromkeys(_strings(plan.get("in_force"))):
                issued_turn = funnel.issued_at.get(directive_id)
                if issued_turn is None or turn <= issued_turn:
                    continue
                funnel.opportunities += 1
                age = turn - issued_turn
                if directive_id in issued:
                    funnel.revised += 1
                    funnel.carried_turns.append(age)
                elif directive_id in followed:
                    funnel.followed += 1
                    funnel.carried_turns.append(age)
                elif directive_id in overrode:
                    funnel.overrode += 1
                else:
                    funnel.unreferenced += 1

    for directive_id in issued:
        if directive_id in funnel.issued_at:
            continue
        if not isinstance(turn, int):
            _error_once(funnel, f"issued directive {directive_id!r} has no issued turn")
            continue
        funnel.issued_at[directive_id] = turn
        funnel.directives_issued += 1


def _summary_records(summary: dict[str, object]) -> list[dict[str, object]] | None:
    """Project the committed strategic-review summary onto the ordinary record vocabulary."""

    required = {
        "run",
        "turns",
        "surfaces",
        "tiers",
        "degraded",
        "opening_issued",
        "review_in_force",
        "review_issued",
        "later_in_force",
        "later_followed",
    }
    if not required.issubset(summary):
        return None
    turns = summary["turns"]
    surfaces = summary["surfaces"]
    tiers = summary["tiers"]
    degraded = summary["degraded"]
    if not all(
        isinstance(value, list) and len(value) >= 3
        for value in (turns, surfaces, tiers, degraded)
    ):
        return None
    rejected = summary.get("rejected")
    rejected_rows = rejected if isinstance(rejected, list) else [[], [], []]
    while len(rejected_rows) < 3:
        rejected_rows.append([])
    identity = summary["run"]
    common = {"game_id": identity}
    issued_at = {directive_id: turns[0] for directive_id in _strings(summary["opening_issued"])}
    for directive_id in _strings(summary["review_issued"]):
        issued_at.setdefault(directive_id, turns[1])

    def statuses(ids: object) -> list[dict[str, object]]:
        return [
            {"id": directive_id, "issued_turn": issued_at[directive_id]}
            for directive_id in _strings(ids)
            if directive_id in issued_at
        ]

    return [
        {
            **common,
            "turn": turns[0],
            "surface_id": surfaces[0],
            "tier": tiers[0],
            "degraded": degraded[0],
            "plan": {
                "in_force": [],
                "followed": [],
                "overrode": [],
                "issued": summary["opening_issued"],
                "rejected": rejected_rows[0],
            },
        },
        {
            **common,
            "turn": turns[1],
            "surface_id": surfaces[1],
            "tier": tiers[1],
            "degraded": degraded[1],
            "directives": statuses(summary["review_in_force"]),
            "plan": {
                "in_force": summary["review_in_force"],
                "followed": [],
                "overrode": [],
                "issued": summary["review_issued"],
                "rejected": rejected_rows[1],
            },
        },
        {
            **common,
            "turn": turns[2],
            "surface_id": surfaces[2],
            "tier": tiers[2],
            "degraded": degraded[2],
            "directives": statuses(summary["later_in_force"]),
            "plan": {
                "in_force": summary["later_in_force"],
                "followed": summary["later_followed"],
                "overrode": summary.get("later_overrode", []),
                "issued": [],
                "rejected": rejected_rows[2],
            },
        },
    ]


def _load(path: Path) -> tuple[list[dict[str, object]], bool]:
    text = path.read_text()
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        projected = _summary_records(whole)
        if projected is not None:
            return projected, True

    records: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records, False


def read(path: Path) -> Report:
    records, published_summary = _load(path)
    report = Report(path=path, published_summary=published_summary)

    for record in records:
        if "surface_id" not in record:
            continue

        report.decisions += 1
        report.tiers[str(record.get("tier"))] += 1
        report.surfaces[str(record.get("surface_id"))] += 1

        turn = record.get("turn")
        if isinstance(turn, int):
            report.turns.append(turn)

        base_id = record.get("base_id")
        if base_id is not None:
            report.has_base_id = True
            if isinstance(turn, int):
                key = (base_id, str(record.get("surface_id")), str(record.get("tier")))
                answer = _answer(record)
                if answer is None:
                    report.no_answer += 1
                report.answers[key].append((turn, answer))

        plan = record.get("plan")
        if isinstance(plan, dict):
            report.plan_records += 1
            followed = [d for d in (plan.get("followed") or []) if isinstance(d, str)]
            if followed:
                report.followed_records += 1
            report.issued_by_agent += len(plan.get("issued") or [])

            # Age needs the directive block, which carries `issued_turn`. The plan block holds
            # ids alone, so a log without the former can say a directive was followed and not
            # how old the reasoning was.
            issued_at: dict[str, int] = {}
            for directive in record.get("directives") or []:
                if isinstance(directive, dict):
                    did, at = directive.get("id"), directive.get("issued_turn")
                    if isinstance(did, str) and isinstance(at, int):
                        issued_at[did] = at
            for did in followed:
                if did in issued_at and isinstance(turn, int):
                    report.directive_ages.append(turn - issued_at[did])
                else:
                    report.ages_unknown += 1

        _observe_funnel(report, record)

    return report


def rework(report: Report) -> dict[tuple[str, str], tuple[int, int]]:
    """Per (surface, tier): consecutive-turn pairs on one base, and how many changed answer.

    Same-turn repeats are skipped, not counted as stability: the engine asks a base several
    times per turn and the adapter replays its cached answer, so a pair inside one turn measures
    the cache and never the brain.

    Split by surface and tier rather than totalled, because a total is uninterpretable. A run
    mixing one surface the brain drives with another the engine answers produces an average of
    two different things, and the average moves when the mix does — which would make the
    headline rate respond to how many surfaces were switched on.
    """
    out: dict[tuple[str, str], tuple[int, int]] = defaultdict(lambda: (0, 0))
    for (_base, surface, tier), series in report.answers.items():
        series.sort(key=lambda item: item[0])
        pairs, changed = out[(surface, tier)]
        for (turn_a, answer_a), (turn_b, answer_b) in zip(series, series[1:], strict=False):
            if turn_a == turn_b:
                continue
            if answer_a is None or answer_b is None:
                continue
            pairs += 1
            if answer_a != answer_b:
                changed += 1
        out[(surface, tier)] = (pairs, changed)
    return {k: v for k, v in out.items() if v[0]}


def emit(report: Report) -> None:
    print(f"=== {report.path} ===")
    if not report.decisions:
        print("  no decision records — nothing to report.\n")
        return

    span = f"turns {min(report.turns)}-{max(report.turns)}" if report.turns else "turns unstated"
    print(f"  {report.decisions} decision records, {span}")
    print("  tiers: " + ", ".join(f"{t}={n}" for t, n in sorted(report.tiers.items())))
    print(
        "  surfaces: "
        + ", ".join(f"{s}={n}" for s, n in sorted(report.surfaces.items(), key=lambda kv: -kv[1]))
    )

    carried, bulk = report.carried, report.bulk
    rate = carried / report.decisions
    print()
    print(f"  RECORD-TIER CARRY {carried}/{report.decisions} = {rate:.3f}")
    print(
        "                  answers produced by an EARLIER turn's reasoning "
        f"(tier in {', '.join(sorted(CARRYING_TIERS))})"
    )
    if bulk:
        print(
            f"                  (+{bulk} at tier `plan` — decided in bulk FOR this turn, which "
            "is not carry;\n                   a plan table is valid for exactly the turn it "
            "names and dies with it)"
        )
    if carried == 0:
        print(
            "                  ZERO, and this is a measurement rather than a missing field: the\n"
            "                  tier is present on every record above. No answer came from the\n"
            "                  queued/deferred carrier; directive carry is measured separately "
            "below."
        )

    print()
    if report.plan_records:
        print(
            f"  DIRECTIVES      {report.followed_records}/{report.plan_records} decisions "
            "followed a standing directive"
        )
        print(f"                  issue events BY THE AGENT during this run: {report.issued_by_agent}")
        if report.issued_by_agent == 0:
            print(
                "                  zero — every directive in force here was written before the "
                "run.\n                  That is na-43h, and it is why the carry above has only "
                "one source."
            )
        if report.directive_ages:
            print(
                f"                  age of followed reasoning: median "
                f"{statistics.median(report.directive_ages):.0f} turns, "
                f"max {max(report.directive_ages)}"
            )
        if report.ages_unknown:
            print(
                f"                  UNANSWERABLE for {report.ages_unknown} followed directive(s):"
                " this log carries the\n                  plan block's ids but no `issued_turn`, "
                "so the age is not in the record."
            )
    else:
        print("  DIRECTIVES      UNANSWERABLE — no record in this log carries a plan block.")

    funnel = report.funnel
    print()
    print(
        f"  CARRY FUNNEL    reviews clean {funnel.reviews_clean}/{funnel.reviews_attempted}; "
        f"agent-issued {funnel.directives_issued}; revisions {funnel.revised}"
    )
    if funnel.eligibility_errors:
        print("  OPPORTUNITY CARRY UNANSWERABLE — " + "; ".join(funnel.eligibility_errors))
    elif not funnel.opportunities:
        print(
            "  OPPORTUNITY CARRY UNANSWERABLE — no later eligible decision carried an "
            "agent-issued directive"
        )
    else:
        print(
            f"  OPPORTUNITY CARRY {funnel.successes}/{funnel.opportunities} = "
            f"{funnel.opportunity_rate:.3f} (followed {funnel.followed}, revised "
            f"{funnel.revised}, overrode {funnel.overrode}, unreferenced "
            f"{funnel.unreferenced})"
        )
        if funnel.carried_turns:
            print(
                f"                  carried turns: n={len(funnel.carried_turns)}, "
                f"sum={sum(funnel.carried_turns)}, "
                f"median={statistics.median(funnel.carried_turns):.0f}, "
                f"max={max(funnel.carried_turns)}"
            )
    if funnel.expiry_observable:
        print(f"                  expired {funnel.expired}")
    else:
        print(
            "                  expiry UNANSWERABLE — records carry no retirement event; "
            "disappearance is not evidence"
        )

    print()
    if report.has_base_id:
        table = rework(report)
        if table:
            print("  REWORK RATE     consecutive-TURN pairs on one base whose answer changed")
            for (surface, tier), (pairs, changed) in sorted(table.items()):
                print(
                    f"                    {surface:<20} {tier:<14} {changed:>4}/{pairs:<5} "
                    f"= {changed / pairs:.3f}"
                )
            det = [v for k, v in table.items() if k[1] == "deterministic"]
            brain = [v for k, v in table.items() if k[1] == "llm"]
            if det and brain:
                det_pairs, det_changed = sum(p for p, _ in det), sum(c for _, c in det)
                brain_pairs, brain_changed = sum(p for p, _ in brain), sum(c for _, c in brain)
                if det_pairs and brain_pairs:
                    print(
                        f"                  brain {brain_changed / brain_pairs:.3f} against a "
                        f"deterministic control of {det_changed / det_pairs:.3f}, same board and"
                        "\n                  same turns. The control is what makes the first "
                        "number readable at all:\n                  some churn is the board "
                        "changing, not the brain forgetting.\n"
                        "                  READ IT AS A BOUND, NOT A MATCHED PAIR. The two rows "
                        "are usually\n                  DIFFERENT SURFACES with different "
                        "answer spaces, so this compares how\n                  much two "
                        "different questions moved on one board — not the same question\n"
                        "                  answered two ways. A matched control needs both "
                        "tiers on ONE surface."
                    )
            print(
                "                  A surface whose answer is the same most turns has a low rate "
                "for\n                  reasons that are not stability — read each row with its "
                "own answer mix."
            )
            if report.no_answer:
                print(
                    f"                  {report.no_answer} record(s) stated no answer and are "
                    "excluded above."
                )
        else:
            print("  REWORK RATE     UNANSWERABLE — no base and surface recurred across turns.")
    else:
        print(
            "  REWORK RATE     UNANSWERABLE — no record carries `base_id`, so two decisions on "
            "the\n                  same base cannot be told from two on different ones. "
            "Orchestrator\n                  decision records omit it; adapter observation logs "
            "carry it."
        )
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("logs", nargs="+", help="one or more decision or observation JSONL logs")
    ap.add_argument(
        "--min-decisions",
        type=int,
        default=30,
        help="refuse a log too short for a rate to mean anything",
    )
    args = ap.parse_args()

    exit_code = 0
    for name in args.logs:
        path = Path(name)
        if not path.is_file():
            print(f"no such log: {path}", file=sys.stderr)
            exit_code = 1
            continue
        report = read(path)
        if report.decisions and report.decisions < args.min_decisions and not report.published_summary:
            print(
                f"=== {path} ===\n  refusing: {report.decisions} decision(s), and a rate needs "
                f"{args.min_decisions}.\n  A handful of replayed observations show the mechanism "
                "ran and say nothing about\n  whether reasoning carried.\n",
                file=sys.stderr,
            )
            exit_code = 1
            continue
        emit(report)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
