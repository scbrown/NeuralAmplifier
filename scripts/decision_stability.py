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

Needs no game: a captured observation from `na-observations.jsonl` is enough.

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

from neural_amplifier.contract import Action, WorldView  # noqa: E402
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
    """Map an adapter observation onto the contract.

    A deliberate bridge, not a second wire format to bless: the adapter currently emits its own
    observation shape, and A1 has it emit the contract directly. Keeping the mapping here rather
    than in the orchestrator package avoids teaching the orchestrator a shape it should never
    learn — it speaks the contract and nothing else (`AGENTS.md` invariant 2).

    Everything the contract does not model is passed through in the engine-dependent sections,
    which is what they exist for.
    """
    actions = [
        Action(id=str(a["id"]), action=str(a.get("name", a["id"])), **_extras(a))
        for a in record.get("action_space", [])
    ]
    scope = "base" if "base_id" in record else "turn"
    passthrough = {
        k: v
        for k, v in record.items()
        if k
        not in {
            "surface_id",
            "engine",
            "turn",
            "faction",
            "faction_id",
            "action_space",
            "action_space_size",
            "tier",
            "applied",
        }
    }
    return WorldView(
        engine=record.get("engine", "thinker"),
        scope=scope,  # type: ignore[arg-type]
        turn=int(record.get("turn", 0)),
        faction=str(record.get("faction") or f"faction-{record.get('faction_id')}"),
        surface_id=record.get("surface_id"),
        action_space=actions,
        economy=passthrough or None,
    )


def _extras(action: dict[str, Any]) -> dict[str, Any]:
    """Cost, role, effect and the turn estimates, carried through onto the Action.

    ``Action`` is ``extra="allow"``, and these fields are the whole reason a decision is decidable
    — dropping them here would measure stability on a world view nobody actually uses.
    """
    return {k: v for k, v in action.items() if k not in {"id", "name"}}


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
    ap.add_argument("-n", "--runs", type=int, default=5)
    ap.add_argument("--brain", default="scripted", choices=["scripted", "claude"])
    ap.add_argument("--quipu", help="quipu-server base URL, to include grounding")
    args = ap.parse_args()

    record = load_observation(args.observations, args.surface)
    world_view = to_world_view(record)

    retriever = None
    if args.quipu:
        from neural_amplifier.datalinks import QuipuRetriever

        retriever = QuipuRetriever(args.quipu, engine=world_view.engine)

    orchestrator = Orchestrator(brain=build_brain(args.brain), retriever=retriever)

    choices: list[str] = []
    utilisations: list[float] = []
    degraded = 0
    reasons: set[str] = set()
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
