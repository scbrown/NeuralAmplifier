#!/usr/bin/env python3
"""Re-derive every published measurement on a host with no game files (na-8qo).

    scripts/score_published.py            # the acceptance check
    scripts/score_published.py --show     # ...and print each table it re-derived

Stiwi's directive is that the eval data has to be usable "when the host doesn't have the game
files". This turns that from a habit into a check that fails.

## What it actually verifies, and why each part is here

**SMAC_PLAY_DIR must be UNSET.** That is the acceptance condition, so the check refuses to run
with it set rather than passing in an environment that cannot demonstrate anything. A green run
on a machine that happens to have the game installed is the exact false pass this bead exists to
prevent.

**Every input must be TRACKED IN GIT, not merely present.** This is the check that catches the
real gap. A scorer reading a file that lives only in someone's working tree or scratchpad passes
on the author's machine and fails on a fresh clone — which is where it will be run. `git
ls-files --error-unmatch` is asked about each path, so "it works for me" and "it works from the
clone" cannot be confused.

**Every run directory must appear in the manifest.** An unlisted directory is a FAILURE rather
than an omission, so a new ladder row cannot be published without declaring how it is
re-derived. That is what makes this cheap to satisfy per row instead of a retrofit.

**A run with nothing to re-derive says so explicitly.** Several directories hold fixtures behind
prose findings, and one (na-373) holds a WITHDRAWN result. An empty `derives` with a `retained`
note is a valid entry; a missing entry is not. The difference matters — "no table is published
from this run" and "nobody wrote down how to score this run" look identical from a file listing.

The child processes run with the environment scrubbed of SMAC_PLAY_DIR, ANTHROPIC_API_KEY,
NA_* and THINKER_DIR, so a scorer that quietly reaches for the install or the model fails here
rather than in front of whoever tries to reproduce us.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "evals" / "published.json"
RUNS = REPO / "evals" / "runs"

#: Anything in the child's environment that could let a scorer cheat by reaching the install,
#: the model, or a sibling checkout. Removed rather than emptied — an empty string is a value.
SCRUB_PREFIXES = ("NA_", "SMAC_", "ANTHROPIC_", "THINKER_", "WINE")


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if not any(k.startswith(p) for p in SCRUB_PREFIXES)}


def tracked(path: Path) -> bool:
    """Is this path committed? Present-in-working-tree is not the question."""
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(path.relative_to(REPO))],
        cwd=REPO, capture_output=True, text=True,
    )
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--show", action="store_true", help="print each re-derived table")
    args = ap.parse_args()

    if os.environ.get("SMAC_PLAY_DIR"):
        print("refusing: SMAC_PLAY_DIR is set. This check only means something on a host with no\n"
              "game install — a pass here would prove nothing. Unset it and run again.",
              file=sys.stderr)
        return 2

    manifest = json.loads(MANIFEST.read_text())
    declared = {r["id"]: r for r in manifest["runs"]}

    failures: list[str] = []
    derived = 0

    on_disk = {p.name for p in RUNS.iterdir() if p.is_dir()} if RUNS.is_dir() else set()
    for name in sorted(on_disk - set(declared)):
        failures.append(f"{name}: a published run directory with no entry in evals/published.json — "
                        "declare how it is re-derived, or that nothing is published from it")
    for name in sorted(set(declared) - on_disk):
        failures.append(f"{name}: declared in evals/published.json but there is no such run directory")

    for run_id in sorted(declared):
        entry = declared[run_id]
        cmds = entry.get("derives") or []
        if not cmds:
            if not entry.get("retained"):
                failures.append(f"{run_id}: nothing to re-derive AND no `retained` note saying why")
            elif args.show:
                print(f"\n=== {run_id}: nothing published to re-derive\n    {entry['retained']}")
            continue

        for cmd in cmds:
            for token in cmd[1:]:
                # A path is a token with a separator in it. Checking every non-flag argument
                # instead reports `7` and `report` as missing files, which is noise that would
                # train a reader to skim this list — and the list is the whole point.
                if token.startswith("-") or "/" not in token:
                    continue
                path = REPO / token
                if not path.exists():
                    failures.append(f"{run_id}: {token} does not exist")
                elif not tracked(path):
                    failures.append(
                        f"{run_id}: {token} is NOT COMMITTED — it would be missing from a fresh "
                        "clone, which is where this has to run")
            proc = subprocess.run([sys.executable, *(str(REPO / cmd[0]),), *cmd[1:]],
                                  cwd=REPO, capture_output=True, text=True, env=child_env())
            label = f"{run_id}: {' '.join(cmd[1:])[:70]}"
            if proc.returncode != 0:
                failures.append(f"{label} exited {proc.returncode}\n      {proc.stderr.strip()[:400]}")
            elif not proc.stdout.strip():
                failures.append(f"{label} produced no output — a silent scorer is not a re-derivation")
            else:
                derived += 1
                if args.show:
                    print(f"\n=== {run_id} ===\n{proc.stdout.rstrip()}")

    print(f"\n{derived} table(s) re-derived from committed data, {len(declared)} run(s) declared, "
          f"SMAC_PLAY_DIR unset")
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("all published measurements survive the game files vanishing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
