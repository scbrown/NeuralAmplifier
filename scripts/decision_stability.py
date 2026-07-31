#!/usr/bin/env python3
"""Run the same world view through the brain N times and report how stable the answer is.

Every decision measurement so far has been a single sample, which cannot distinguish a considered
decision from a coin flip that happened to land on a legal option. Two runs on materially the same
`base.production` world view chose differently — and because the prompts were not identical, we
could not tell "the model is unstable" from "the prompts differed". This makes that a number.

Three things it gives that nothing else does:

* **A stability figure per surface**, so "this surface is decidable from what we send" stops being
  an opinion.
* **A regression check on world-view changes.** If adding a field makes decisions *more*
  consistent, that field earned its place. If it changes nothing, it is costing tokens.
* **A model comparison on identical input**, with no game running.

Needs no game: a captured world view from `na-observations.jsonl` is enough. The
adapter emits the contract directly, so a record is parsed, not translated.

    scripts/decision_stability.py OBSERVATIONS.jsonl --surface base.production -n 5

The default brain is the scripted one, which is deterministic — so a default run reports 1.00 and
verifies the *harness*, not the model. Point `--brain claude` at a real model to measure anything
interesting, and note that does make paid calls.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "orchestrator" / "src"))

from neural_amplifier.contract import WorldView  # noqa: E402
from neural_amplifier.orchestrator import Orchestrator  # noqa: E402


def load_observation(path: Path, surface: str) -> dict[str, Any]:
    """The most recent observation for a surface.

    Most recent rather than first: the adapter appends, so the last line for a surface is the
    freshest game state, and a stale early-game world view is a much less interesting test.
    """
    found: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # A partially written final line is expected — the adapter flushes per record but a
            # crash mid-write costs one line. Skipping is correct; failing is not.
            continue
        if record.get("surface_id") == surface and record.get("action_space"):
            found = record
    if found is None:
        raise SystemExit(f"no {surface} observation with an action space in {path}")
    return found


def to_world_view(record: dict[str, Any]) -> WorldView:
    """Parse an adapter observation as a contract world view.

    There used to be a bridge here — some sixty lines mapping the adapter's own record shape
    onto the contract, renaming ``name`` to ``action``, digging metrics out of ``base_state``
    and ``faction_state``, and deriving each action's ``effects`` from its ``cost``/``cost_unit``
    pair. It existed because the adapter emitted a shape of its own and the orchestrator must
    never learn an engine's field layout (``AGENTS.md`` invariant 2), so the knowledge had to
    live somewhere that was neither.

    The adapter now emits the contract directly, so the mapping is gone rather than moved. That
    is the better place for it by invariant 2's own logic: the adapter is the one component that
    is *allowed* to know both, and a bridge that only this script used meant the world view
    measured here was never quite the one a real game would send.

    A record from before that change will fail here, which is correct — it is a different wire
    format, and quietly coercing it would make a stability number incomparable with the run
    beside it.
    """
    return WorldView.model_validate(record)


def build_brain(kind: str) -> Any:
    if kind == "scripted":
        from neural_amplifier.brain import ScriptedBrain

        return ScriptedBrain()
    if kind == "claude":
        # ClaudeBrain directly rather than service.build_brain, which gates on NA_BRAIN=claude so
        # that tests and CI cannot make a paid call by accident. Here the --brain flag IS the
        # explicit opt-in, so requiring the env var as well would be a second lock on the same door.
        from neural_amplifier.brain import ClaudeBrain

        return ClaudeBrain()
    raise SystemExit(f"unknown brain: {kind}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("observations", type=Path)
    ap.add_argument("--surface", default="base.production")
    # Ten, not five. Five reported 1.00 for base.hurry and the very next sample disagreed; the
    # same surface splits 6/4 over ten runs. Five samples cannot tell stable from lucky, and the
    # whole point of this harness is to stop guessing about that.
    ap.add_argument("-n", "--runs", type=int, default=10)
    ap.add_argument("--brain", default="scripted", choices=["scripted", "claude"])
    ap.add_argument("--quipu", help="quipu-server base URL, to include grounding")
    ap.add_argument(
        "--plan",
        type=Path,
        help="directive store JSON, to put a standing plan in front of the decision",
    )
    args = ap.parse_args()

    record = load_observation(args.observations, args.surface)
    world_view = to_world_view(record)

    retriever = None
    if args.quipu:
        from neural_amplifier.datalinks import QuipuRetriever

        retriever = QuipuRetriever(args.quipu, engine=world_view.engine)

    # The guard built exactly as the service builds it. Constructing the orchestrator directly
    # used to skip it entirely, so every run this harness ever recorded carried hank_absent —
    # which reads in a record as "Hank was down", not as "nobody asked it".
    from neural_amplifier.service import build_guard

    plan = None
    if args.plan:
        from neural_amplifier.directives import DirectiveStore

        plan = DirectiveStore(args.plan)

    orchestrator = Orchestrator(
        brain=build_brain(args.brain),
        retriever=retriever,
        guard=build_guard(retriever),  # type: ignore[arg-type]
        plan=plan,
    )

    choices: list[str] = []
    utilisations: list[float] = []
    degraded = 0
    reasons: set[str] = set()
    advisories: collections.Counter[str] = collections.Counter()
    followed: collections.Counter[str] = collections.Counter()
    overrode: collections.Counter[str] = collections.Counter()
    unmeasurable: collections.Counter[str] = collections.Counter()
    attentions: list[float] = []
    for _ in range(args.runs):
        result = orchestrator.decide(world_view)
        picked = [c.action_id for c in result.orders.choices]
        choices.append(",".join(picked) if picked else "<none>")
        used = result.record.knowledge.utilisation
        if used is not None:
            utilisations.append(used)
        # A brain that failed still returns a safe answer — that is the point of degrading rather
        # than stalling. But it makes stability meaningless: the fallback is one fixed answer, so a
        # fully broken brain measures 1.00. This went unnoticed once, when the anthropic package was
        # missing and five fallbacks were reported as a perfectly stable model.
        if result.record.degraded:
            degraded += 1
            if result.record.degrade_reason:
                reasons.add(result.record.degrade_reason)
        # Counted, not just listed: a citation problem that shows up on one run of five is a
        # model wobbling, and one that shows up on all five is a retrieval or ontology bug.
        # Those want different fixes, and only the count tells them apart.
        for advisory in result.record.knowledge.advisories:
            advisories[advisory] += 1
        block = result.record.plan
        for did in block.followed:
            followed[did] += 1
        for did in block.overrode:
            overrode[did] += 1
        for did in block.unmeasurable:
            unmeasurable[did] += 1
        if block.attention is not None:
            attentions.append(block.attention)

    counts = collections.Counter(choices)
    top, top_n = counts.most_common(1)[0]

    print(f"surface        {args.surface}")
    print(f"base/faction   {record.get('base') or record.get('faction')} · turn {world_view.turn}")
    print(f"options        {len(world_view.action_space)}")
    print(f"brain          {orchestrator.brain.name} · {args.runs} runs")
    print()
    for choice, count in counts.most_common():
        bar = "#" * count
        print(f"  {choice:<28} {count:>3}  {bar}")
    print()
    # Modal share, not entropy: the question is "would this decision be the same next turn",
    # and the share of the most common answer says that directly.
    if degraded:
        print(f"!! DEGRADED     {degraded} of {args.runs} runs — the brain did not decide these")
        for reason in sorted(reasons):
            print(f"   reason       {reason}")
        if degraded == args.runs:
            print("   stability below is meaningless: the fallback is a single fixed answer")
        print()

    print(f"stability      {top_n / args.runs:.2f}  (modal answer {top!r})")
    print(f"distinct       {len(counts)} of {args.runs} runs")
    if utilisations:
        print(f"utilisation    mean {statistics.mean(utilisations):.2f}")
    else:
        print("utilisation    n/a  (no grounding — pass --quipu to measure it)")

    if attentions:
        print(f"plan attention mean {statistics.mean(attentions):.2f}")
        # Followed and overrode side by side, because either number alone misleads. A directive
        # that is always overridden was mispriced; one that is never mentioned is steering nothing.
        for did in sorted(set(followed) | set(overrode) | set(unmeasurable)):
            bits = []
            if followed[did]:
                bits.append(f"followed {followed[did]}/{args.runs}")
            if overrode[did]:
                bits.append(f"overrode {overrode[did]}/{args.runs}")
            if unmeasurable[did]:
                bits.append(f"UNMEASURABLE {unmeasurable[did]}/{args.runs}")
            print(f"  {did:<28} {', '.join(bits)}")

    if advisories:
        print()
        print("guard          citation advisories (runs affected)")
        for advisory, count in advisories.most_common():
            print(f"  {count}/{args.runs}  {advisory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
