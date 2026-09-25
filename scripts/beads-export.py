#!/usr/bin/env python3
"""Export the beads store to the tracked JSONL, and refuse to write a regression.

## Why this exists

The repository disables `bd` auto-export. When enabled it writes directly into the checkout
that ran the lifecycle command, which made ordinary store updates dirty the shared deploy
checkout and caused its correct clean-tree guard to refuse every automatic deployment until a
human reconciled the projection. `bd` also unconditionally appends status changes to
`.beads/interactions.jsonl`, so that live audit file is ignored and this command copies it to
the tracked `.beads/interactions.snapshot.jsonl`. JSONL is therefore a deliberate tracked
artifact, refreshed only through this command from an owned worktree.

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

## Now br, not bd

The tracker moved to br, and bd is retired and refuses every command, which failed `just check`
for everyone. The history above is about bd and is kept because the lesson (never trust the
auto path) still holds. br has no `export -o`: `br sync --flush-only` writes beside its own
`.beads`, so this takes a consistent SQLite backup of the store into a temp `.beads` and flushes
there. Nothing is written outside that temp directory. br also redacts owner emails in exports.

## What this does

Always a forced export into a temp store copy, never the auto path. Then it diffs the new export against the
committed JSONL and **refuses to install a regression**, because the failure this guards is
one-directional: work disappearing.

The id-set check alone cannot catch it — a stale export has the identical id set, which is why
the two prior repairs did not find the cause. It takes a per-record field diff.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRACKED = REPO / ".beads" / "issues.jsonl"
AUDIT_SNAPSHOT = REPO / ".beads" / "interactions.snapshot.jsonl"


# The tracker moved from bd to br (aegis-sgvm5q). bd is retired on this host and refuses
# every command, so the old calls failed `just check` for everyone. Every br call below passes
# --no-auto-import and --no-auto-flush: this script reads the store and must never import a
# working-tree JSONL into it, or flush into a checkout.
BR = ["br", "--no-auto-import", "--no-auto-flush"]


def main_checkout() -> Path:
    """The checkout that owns the store. A worktree has no store of its own, and br run
    there would auto-import the worktree's JSONL into a FRESH store, making this check compare
    the tracked file to itself and pass every time."""
    proc = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=REPO, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot locate the main checkout: {proc.stderr.strip()}")
    return Path(proc.stdout.strip()).parent


def store() -> dict:
    """br's own answer for where the store lives: `path` (the .beads dir) and `database_path`."""
    proc = subprocess.run(BR + ["where", "--json"], cwd=main_checkout(), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"br where failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def live_audit_path() -> Path:
    """The live audit log, which is in the main checkout's .beads for worktrees."""
    return Path(store()["path"]) / "interactions.jsonl"


# The tracked JSONL is a PUBLIC projection of the store. br already redacts owner emails from it;
# local home paths are the same kind of leak (a comment quoting a worktree path, a
# source_repo_path field). Normalize them in the projection only; the store keeps its data.
HOME_PATH = re.compile(r"/home/[A-Za-z0-9._-]+/")


def publishable(value):
    """Replace absolute home-directory prefixes with ~/ in every string, recursively."""
    if isinstance(value, str):
        return HOME_PATH.sub("~/", value)
    if isinstance(value, list):
        return [publishable(item) for item in value]
    if isinstance(value, dict):
        return {key: publishable(item) for key, item in value.items()}
    return value


def publish_lines(path: Path) -> None:
    """Rewrite only the JSONL lines that contain a home path, so br's own bytes survive
    everywhere else and the diff stays reviewable."""
    lines = []
    for line in path.read_text().splitlines(keepends=True):
        if HOME_PATH.search(line):
            record = publishable(json.loads(line))
            line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        lines.append(line)
    path.write_text("".join(lines))


def fresh_export(dest: Path) -> None:
    """Export the store to ``dest`` without writing anything outside a temp directory.

    br has no `export -o`; `br sync --flush-only` writes the JSONL beside its own .beads.
    So take a consistent SQLite backup of the store into a temp .beads and flush THERE.
    Measured before this was written: an export from a backup-API copy is byte-identical to
    one from the live store's files.
    """
    where = store()
    with tempfile.TemporaryDirectory() as tmp:
        beads = Path(tmp) / ".beads"
        beads.mkdir()
        db = beads / Path(where["database_path"]).name
        src = sqlite3.connect(f"file:{where['database_path']}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(db)
            src.backup(dst)
            dst.close()
        finally:
            src.close()
        for name in ("metadata.json", "config.yaml"):
            if (Path(where["path"]) / name).exists():
                shutil.copy2(Path(where["path"]) / name, beads / name)
        proc = subprocess.run(
            ["br", "--db", str(db), "--no-auto-import", "sync", "--flush-only"],
            cwd=tmp, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"br sync --flush-only failed: {proc.stderr.strip()}")
        shutil.copyfile(beads / "issues.jsonl", dest)
    publish_lines(dest)


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


def committed_audit() -> dict[str, dict]:
    proc = subprocess.run(
        ["git", "show", "HEAD:.beads/interactions.snapshot.jsonl"],
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


def audit_regressions(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    problems = []
    for lost in sorted(set(before) - set(after)):
        problems.append(f"audit {lost}: present in committed snapshot and absent from live log")
    for changed in sorted(set(before) & set(after)):
        if before[changed] != after[changed]:
            problems.append(f"audit {changed}: append-only record changed")
    return problems


def merge_audit(
    snapshot: dict[str, dict], live_tail: dict[str, dict]
) -> tuple[dict[str, dict], list[str]]:
    """Append the rotated live tail without pretending it is the whole audit history.

    ``interactions.jsonl`` is deliberately rotated after snapshotting.  Absence from that
    short tail therefore says nothing about a historical record.  An id collision, however,
    must be byte-for-byte equivalent or the append-only contract has been violated.
    """
    problems = []
    for interaction_id in sorted(set(snapshot) & set(live_tail)):
        if snapshot[interaction_id] != live_tail[interaction_id]:
            problems.append(f"audit {interaction_id}: append-only record changed")
    return {**snapshot, **live_tail}, problems


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

    if shutil.which("br") is None:
        if args.check:
            print("beads export check skipped: br is not installed")
            return 0
        print("br is required to refresh the beads export", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as handle:
        fresh_path = Path(handle.name)

    # ALWAYS a forced export into a temp store copy, never the auto path (see fresh_export).
    try:
        fresh_export(fresh_path)
    except (RuntimeError, OSError, sqlite3.Error, KeyError, ValueError) as error:
        print(f"br export failed: {error}", file=sys.stderr)
        return 1

    fresh = load(fresh_path)
    audit_path = live_audit_path()
    audit_base = committed_audit()
    live_tail = publishable(load(audit_path)) if audit_path.exists() else {}
    fresh_audit, audit_merge_problems = merge_audit(audit_base, live_tail)
    if not fresh:
        # An empty export over a populated tracker is the worst possible write.
        print("refusing to install an empty export", file=sys.stderr)
        return 1

    if args.check:
        current = load(TRACKED) if TRACKED.exists() else {}
        current_audit = load(AUDIT_SNAPSHOT) if AUDIT_SNAPSHOT.exists() else {}
        if current == fresh and current_audit == fresh_audit:
            print("beads export is current")
            return 0
        stale = [i for i in fresh if current.get(i) != fresh.get(i)]
        stale_audit = [i for i in fresh_audit if current_audit.get(i) != fresh_audit.get(i)]
        print(
            f"beads export is STALE — {len(stale)} issue record(s) differ from the store: "
            f"{', '.join(sorted(stale)[:8])}",
            file=sys.stderr,
        )
        print(
            f"audit snapshot is STALE — {len(stale_audit)} record(s) differ from the live log",
            file=sys.stderr,
        )
        print("run `just beads-export` to refresh", file=sys.stderr)
        return 1

    problems = regressions(committed(), fresh)
    problems.extend(audit_merge_problems)
    problems.extend(audit_regressions(committed_audit(), fresh_audit))
    if problems:
        print("refusing to write: the export loses work relative to HEAD", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(f"\nfresh export left at {fresh_path} for inspection", file=sys.stderr)
        return 1

    before = load(TRACKED) if TRACKED.exists() else {}
    TRACKED.write_text(fresh_path.read_text())
    AUDIT_SNAPSHOT.write_text(
        "".join(
            json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
            for record in fresh_audit.values()
        )
    )
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
