#!/usr/bin/env python3
"""Check what a LIVE adapter actually populates on the contract.

    scripts/check_live_world_view.py na-observations.jsonl

The bug this exists to catch (na-wzw) was invisible to every other check we had. The adapter
emitted `recent_builds`; the contract declared `history`; the system prompt explained `history`.
Nothing mapped between them, so `WorldView.history` was None on every real decision — and
nothing failed, because `WorldView` allows extras and the whole payload reaches the prompt
regardless. A unit test on a hand-written fixture passed, because the fixture was written to
match the contract rather than captured from the adapter.

So the question this asks is deliberately narrow and not asked anywhere else: **after parsing
real adapter output, which typed fields are actually populated?** A field that is always None
across a live capture is either an adapter gap or a contract field nobody feeds, and both are
worth knowing. Extras are reported too, because an extra that looks like a typed field under a
different name is the exact shape of this bug.

Exit status is 1 if a field named on the command line with --require is absent everywhere.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "orchestrator" / "src"))

from neural_amplifier.contract import WorldView  # noqa: E402

#: Typed fields an adapter is responsible for. Orchestrator-injected fields (`grounding`,
#: `advisories`, `directives`, `tradeoffs`) are excluded: they are absent in a raw capture by
#: design, and flagging them would bury the ones that mean something.
ADAPTER_FIELDS = (
    "history",
    "fairness",
    "action_space",
    "metrics",
    "subjects",
    "contacts",
    "scores",
    "economy",
    "map",
    "units",
    "bases",
    "deltas",
    "year",
    "surface_id",
    "trace",
    "memory",
)


def populated(value: Any) -> bool:
    """Empty is not populated. A field the adapter emits as `[]` on every row tells the brain
    nothing, and counting it as present is how a dead field looks alive."""
    return value is not None and value != [] and value != {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("observations", type=Path)
    ap.add_argument("--surface", default="base.production")
    ap.add_argument(
        "--require",
        action="append",
        default=[],
        help="fail unless this typed field is populated on at least one row",
    )
    args = ap.parse_args()

    rows: list[dict[str, Any]] = []
    for line in args.observations.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # partial final line: the adapter flushes per record
        if record.get("surface_id") == args.surface:
            rows.append(record)
    if not rows:
        raise SystemExit(f"no {args.surface} rows in {args.observations}")

    counts: collections.Counter[str] = collections.Counter()
    extras: collections.Counter[str] = collections.Counter()
    rejected: list[str] = []
    parsed: list[WorldView] = []
    for record in rows:
        try:
            view = WorldView.model_validate(record)
        except Exception as exc:  # noqa: BLE001 — a rejected row is the finding, not a crash
            rejected.append(f"turn {record.get('turn')} {record.get('base')}: {exc}")
            continue
        parsed.append(view)
        for name in ADAPTER_FIELDS:
            if populated(getattr(view, name, None)):
                counts[name] += 1
        for key in view.model_dump(exclude_none=True):
            if key not in WorldView.model_fields:
                extras[key] += 1

    print(f"surface        {args.surface}")
    print(f"rows           {len(rows)} parsed {len(parsed)} rejected {len(rejected)}")
    for line in rejected[:5]:
        print(f"  !! {line}")
    print()
    print("typed adapter fields populated:")
    for name in ADAPTER_FIELDS:
        n = counts[name]
        mark = "  " if n else "!!"
        print(f"  {mark} {name:<14} {n}/{len(parsed)}")
    if extras:
        print()
        print("engine extras (fine — unless one shadows a typed field above):")
        for key, n in extras.most_common(20):
            print(f"     {key:<20} {n}/{len(parsed)}")

    missing = [f for f in args.require if not counts[f]]
    if args.require:
        print()
        for field in args.require:
            print(f"required       {field}: {'PRESENT' if counts[field] else 'ABSENT'}"
                  f"  ({counts[field]}/{len(parsed)} rows)")
    if rejected:
        return 1
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
