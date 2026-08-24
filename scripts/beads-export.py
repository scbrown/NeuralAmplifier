#!/usr/bin/env python3
"""Export the beads store to the tracked JSONL, and refuse to write a regression.

## Why this exists

The repository disables `bd` auto-export. When enabled it writes directly into the checkout
that ran the lifecycle command, which made ordinary store updates dirty the shared deploy
checkout and caused its correct clean-tree guard to refuse every automatic deployment until a
human reconciled the projection. JSONL is therefore a deliberate tracked artifact, refreshed
only through this command from an owned worktree.

There is a second reason not to use auto-export: it is **gated**. A
second write shortly after the first does not export, `bd` exits 0, and nothing says so — so the
git-tracked tracker silently lags the store. Measured on this repo (na-2a9):

    bd comment na-2a9 "..."      -> issues.jsonl updated, export-state.json timestamp advances
    bd comment na-2a9 "..."      -> issues.jsonl UNCHANGED, exit 0, no warning
    bd export -o /tmp/fresh      -> store has BOTH comments; the tracked file has one

That is the mechanism behind two prior hand-repaired incidents on this repo, and it bit twice
more in one session: an `--append-notes` exported, the `close` seconds later did not, and the
commit carried new notes with a stale status. It is invisible in review, because a stale export
is a well-formed file with the right id count and the wrong field values.

This bead's own history is a warning about diagnosing it: the original report blamed
`bd export` with no `-o` for writing a stale file. That was wrong — no `-o` writes to stdout and
touches nothing — and the retraction says so at length. The gate is on the *auto*-export, not on
any `bd export` invocation.

## What this does

Always a forced `bd export -o`, never the auto path. Then it diffs the new export against the
committed JSONL and **refuses to install a regression**, because the failure this guards is
one-directional: work disappearing.

The id-set check alone cannot catch it — a stale export has the identical id set, which is why
the two prior repairs did not find the cause. It takes a per-record field diff.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRACKED = REPO / ".beads" / "issues.jsonl"


def load(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        out[record["id"]] = record
    return out


def committed() -> dict[str, dict]:
    """The JSONL as git has it — the baseline a regression would be measured against.

    Deliberately HEAD rather than the working tree: comparing against the working tree would
    compare a stale file to itself and pass every time, which is the exact failure being
    guarded.
    """
    proc = subprocess.run(
        ["git", "show", "HEAD:.beads/issues.jsonl"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {}
    out: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        if line.strip():
            record = json.loads(line)
            out[record["id"]] = record
    return out


def regressions(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    """Only ever-backwards changes. Additions and edits are normal; losses are not."""
    problems: list[str] = []

    for lost in sorted(set(before) - set(after)):
        problems.append(f"{lost}: present in the committed JSONL and absent from the export")

    for issue_id in sorted(set(before) & set(after)):
        old, new = before[issue_id], after[issue_id]

        # A close that un-closes. The signature of both prior incidents.
        if old.get("status") == "closed" and new.get("status") != "closed":
            problems.append(
                f"{issue_id}: closed -> {new.get('status')!r}; a reopen is a human action"
            )

        # Notes and comments only grow. `bd update --notes` REPLACES, so a shrink is either
        # that mistake or a stale record — both worth stopping.
        old_notes = old.get("notes") or ""
        new_notes = new.get("notes") or ""
        if len(new_notes) < len(old_notes):
            problems.append(
                f"{issue_id}: notes shrank {len(old_notes)} -> {len(new_notes)} chars "
                "(--notes replaces; use --append-notes)"
            )

        old_comments = len(old.get("comments") or [])
        new_comments = len(new.get("comments") or [])
        if new_comments < old_comments:
            problems.append(f"{issue_id}: comments {old_comments} -> {new_comments}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the tracked JSONL is stale; write nothing, exit 1 if it is",
    )
    args = parser.parse_args()

    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as handle:
        fresh_path = Path(handle.name)

    # ALWAYS -o. The auto-export is the thing being worked around, and `bd export` with no -o
    # writes to stdout and touches no file at all.
    proc = subprocess.run(
        ["bd", "export", "-o", str(fresh_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"bd export failed: {proc.stderr.strip()}", file=sys.stderr)
        return 1

    fresh = load(fresh_path)
    if not fresh:
        # An empty export over a populated tracker is the worst possible write.
        print("refusing to install an empty export", file=sys.stderr)
        return 1

    if args.check:
        current = load(TRACKED) if TRACKED.exists() else {}
        if current == fresh:
            print("beads export is current")
            return 0
        stale = [i for i in fresh if current.get(i) != fresh.get(i)]
        print(
            f"beads export is STALE — {len(stale)} record(s) differ from the store: "
            f"{', '.join(sorted(stale)[:8])}",
            file=sys.stderr,
        )
        print("run `just beads-export` to refresh", file=sys.stderr)
        return 1

    problems = regressions(committed(), fresh)
    if problems:
        print("refusing to write: the export loses work relative to HEAD", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(f"\nfresh export left at {fresh_path} for inspection", file=sys.stderr)
        return 1

    before = load(TRACKED) if TRACKED.exists() else {}
    TRACKED.write_text(fresh_path.read_text())
    fresh_path.unlink(missing_ok=True)

    changed = [i for i in fresh if before.get(i) != fresh.get(i)]
    added = sorted(set(fresh) - set(before))
    if changed:
        print(f"exported {len(fresh)} issues; {len(changed)} changed", end="")
        print(f", {len(added)} new" if added else "")
        for issue_id in sorted(changed)[:10]:
            status = fresh[issue_id].get("status")
            print(f"  {issue_id} ({status})")
        if len(changed) > 10:
            print(f"  ... and {len(changed) - 10} more")
    else:
        print(f"exported {len(fresh)} issues; nothing changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
