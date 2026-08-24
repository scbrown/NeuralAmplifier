#!/usr/bin/env python3
"""Find NA run orchestrators that outlived their run, and the tmpfs copies they pin.

A finished run leaves two things behind and nothing reaps either: the ``neural-amplifier
serve`` process, and the ~2.8GB game/Wine copy the run worked from. When that copy lives
under ``/tmp`` it is tmpfs, which is RAM, so the leak is not disk — it is memory. Measured
on this host 2026-08-24: ``/tmp`` at 25G/80% with swap 100% exhausted (236Ki free), and a
22-hour orchestrator holding a 2.8G tree open nine hours after its last decision.

THE RULE THIS TOOL EXISTS TO ENCODE — do not select by age.

The obvious heuristic is "old means dead", and on this host it is wrong in both
directions. A frozen run's DATA directory was 31 hours old and was the raw evidence
behind a published result; it was old precisely BECAUSE it was frozen. Meanwhile a live
250-turn ladder row had been running under three hours and would have looked young by the
same measure only by luck. Age is not evidence of anything here.

So every verdict below is derived from EVIDENCE OF WORK:

  * does a game process still exist for this run,
  * when was this run's ``decisions.jsonl`` last written,
  * does that decision log still exist at all.

WHAT IS AUTO-REAPED, AND WHY ONLY THAT

Exactly one class: ``orphan-data-gone`` — the run directory the orchestrator was started
against no longer exists. That is unambiguous. The process is serving a run whose data is
gone; it cannot be doing work, and it cannot be holding anything anyone wants. Measured
here: two orchestrators at 27h and 25h whose decision logs had already been deleted.

Everything else is REPORTED and never touched, including a process idle for many hours
with no game attached. That case looks reapable and is not: a run can be paused, a brain
can be waiting on a slow upstream, and another agent's session can own it. A tool that
kills those trades a memory leak for lost work, which is a bad trade at any size.

DATA IS NEVER DELETED. tmpfs run copies are reported with their size and a suggested
destination on disk. Moving them is a one-line operation a human or agent can run after
looking; deleting them is not reversible and this tool does not do it.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

#: A run laid out as <run>/orch/decisions.jsonl and <run>/play, which is what the play
#: scripts create. The orchestrator knows its decision log; the game knows its play dir.
DECISIONS = "decisions.jsonl"

#: How long a run may go without writing a decision before it is merely REPORTED as idle.
#: Deliberately generous, and deliberately not a reaping threshold: nothing is killed for
#: being idle, so this number cannot cost anyone their work by being too small.
DEFAULT_IDLE_MINUTES = 240


@dataclass
class Observation:
    """One orchestrator, as read off the host."""

    port: str
    pids: list[int] = field(default_factory=list)
    run_dir: str | None = None
    decisions_path: str | None = None
    #: None when the file does not exist — which is NOT the same as "idle for a long time",
    #: and the difference is the whole basis of the auto-reap decision.
    idle_seconds: int | None = None
    decisions_exists: bool = False
    game_run_dirs: tuple[str, ...] = ()
    uptime_seconds: int = 0


@dataclass
class Verdict:
    state: str
    why: str
    reapable: bool


def classify(obs: Observation, idle_seconds_threshold: int) -> Verdict:
    """Decide what an orchestrator is, from evidence of work alone.

    Pure so it can be proven without a host: `--selftest` drives every branch through
    synthetic observations. A classifier that can only be exercised by having a real
    orphan lying around is one nobody checks before trusting it with SIGTERM.

    Order matters. A live game outranks every other signal, because a run that is
    genuinely playing may still be between decisions.
    """
    if obs.run_dir is None:
        return Verdict("unknown", "no NA_DECISION_LOG in the environment", False)

    if obs.run_dir in obs.game_run_dirs:
        return Verdict("live-game", "a game process is running for this run", False)

    if not obs.decisions_exists:
        # The only auto-reapable class. The run directory is gone, so there is no work in
        # progress and nothing being held that anyone could want.
        return Verdict(
            "orphan-data-gone", f"run directory no longer exists: {obs.run_dir}", True
        )

    if obs.idle_seconds is None:
        return Verdict("unknown", "decision log exists but could not be read", False)

    if obs.idle_seconds <= idle_seconds_threshold:
        return Verdict("active", f"decision written {obs.idle_seconds // 60}m ago", False)

    # Looks dead, is NOT reaped. A paused run, a brain blocked on a slow upstream, and a
    # finished run are indistinguishable from here, and only one of them is safe to kill.
    return Verdict(
        "idle-no-game",
        f"idle {obs.idle_seconds // 60}m with no game process — REPORT ONLY, not reaped",
        False,
    )


def _port_of(cmd: str) -> str:
    """The port an orchestrator serves, from a command line that may be shell-quoted.

    The launch wrapper is a ``sh -c`` whose own command line contains the quoted inner
    command, so the raw token can arrive as ``"8088"`` rather than ``8088``. Measured on
    this host: that produced a PHANTOM seventh orchestrator on port ``"8088"`` and split
    the real group in two — and a split group is the dangerous half of this bug, because
    reaping would then signal some of a run's processes and leave the rest serving.

    Stripping quotes is what makes the wrapper join the group it belongs to.
    """
    parts = cmd.split()
    for i, token in enumerate(parts):
        candidate = ""
        if token == "--port" and i + 1 < len(parts):
            candidate = parts[i + 1]
        elif token.startswith("--port="):
            candidate = token.split("=", 1)[1]
        candidate = candidate.strip("\"'")
        if candidate.isdigit():
            return candidate
    return ""


def _environ(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        # A process that exits mid-enumeration is ordinary, not an error. Observed on the
        # first real run of this tool.
        return {}
    out: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if b"=" in entry:
            key, _, value = entry.partition(b"=")
            out[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return out


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace"
        )
    except OSError:
        return ""


def _uptime_seconds(pid: int) -> int:
    try:
        return int(time.time() - Path(f"/proc/{pid}").stat().st_mtime)
    except OSError:
        return 0


def _pids_matching(pattern: str) -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in out.split() if line.isdigit()]


def _run_dir_of_decision_log(path: str) -> str | None:
    """<run>/orch/decisions.jsonl -> <run>."""
    parent = os.path.dirname(os.path.dirname(path))
    return parent or None


def game_run_dirs() -> tuple[str, ...]:
    """Run directories that still have a game process attached.

    Read from the GAME process, not the orchestrator: measured on this host, every
    orchestrator had SMAC_PLAY_DIR unset while the wine process carried it. Keying on the
    orchestrator's own environment would have found no live games at all and marked a
    running 250-turn ladder row reapable.
    """
    found: set[str] = set()
    for pid in _pids_matching("terranx|wine|xvfb-run"):
        play = _environ(pid).get("SMAC_PLAY_DIR")
        if play:
            run = os.path.dirname(play.rstrip("/"))
            if run:
                found.add(run)
    return tuple(sorted(found))


def observe() -> list[Observation]:
    games = game_run_dirs()
    by_port: dict[str, Observation] = {}
    for pid in _pids_matching("neural-amplifier serve"):
        cmd = _cmdline(pid)
        port = _port_of(cmd)
        if not port:
            continue
        obs = by_port.setdefault(port, Observation(port=port, game_run_dirs=games))
        obs.pids.append(pid)
        obs.uptime_seconds = max(obs.uptime_seconds, _uptime_seconds(pid))
        # Several pids share a port (a uv wrapper plus its python child, sometimes a bash
        # parent). Any one of them may carry the environment; take the first that does,
        # and never assume the wrapper knows what the child knows.
        if obs.decisions_path is None:
            log = _environ(pid).get("NA_DECISION_LOG")
            if log:
                obs.decisions_path = log
                obs.run_dir = _run_dir_of_decision_log(log)
    for obs in by_port.values():
        if obs.decisions_path and os.path.exists(obs.decisions_path):
            obs.decisions_exists = True
            try:
                obs.idle_seconds = int(time.time() - os.stat(obs.decisions_path).st_mtime)
            except OSError:
                obs.idle_seconds = None
    return [by_port[k] for k in sorted(by_port)]


def tmpfs_run_copies(root: str = "/tmp") -> list[tuple[str, int]]:
    """Run-shaped directories sitting on tmpfs, largest first.

    Reported only. A run copy under /tmp is RAM, and the fix is to MOVE it to disk, never
    to delete it — one of these was the sole copy of a published result's raw data.
    """
    found: list[tuple[str, int]] = []
    for dirpath, dirnames, _ in os.walk(root):
        if {"orch", "game"} & set(dirnames) or {"orch", "wine"} & set(dirnames):
            size = 0
            for base, _, files in os.walk(dirpath):
                for name in files:
                    try:
                        size += os.lstat(os.path.join(base, name)).st_size
                    except OSError:
                        continue
            found.append((dirpath, size))
            dirnames[:] = []  # do not descend into a run we have already sized
        # Never walk into another agent's business more than one level deep by accident.
        if dirpath.count(os.sep) - root.count(os.sep) > 4:
            dirnames[:] = []
    return sorted(found, key=lambda item: -item[1])


def reap(obs: Observation, dry_run: bool) -> str:
    """Stop one orchestrator's whole process group, children first.

    Children first because the uv wrapper respawns nothing but does hold the port; killing
    the wrapper alone leaves the python child serving, and killing the child alone leaves a
    wrapper that exits on its own schedule. Highest pid first is a good enough proxy for
    "child" here and avoids reading the whole process tree.
    """
    if dry_run:
        return f"WOULD stop pids {sorted(obs.pids, reverse=True)}"
    stopped: list[int] = []
    for pid in sorted(obs.pids, reverse=True):
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            return f"REFUSED: no permission to signal pid {pid}"
    time.sleep(3)
    still = [pid for pid in stopped if Path(f"/proc/{pid}").exists()]
    if still:
        return f"stopped {stopped}, STILL UP {still} (not escalating to SIGKILL)"
    return f"stopped {stopped}"


def selftest() -> int:
    """Prove the classifier discriminates, including that it REFUSES the tempting cases."""
    threshold = DEFAULT_IDLE_MINUTES * 60
    cases = [
        (
            "a running game is live even with no recent decision",
            Observation(port="1", run_dir="/r/a", decisions_exists=True,
                        idle_seconds=99999, game_run_dirs=("/r/a",)),
            "live-game", False,
        ),
        (
            "data gone is the one auto-reapable class",
            Observation(port="2", run_dir="/r/b", decisions_exists=False),
            "orphan-data-gone", True,
        ),
        (
            "recent decision is active",
            Observation(port="3", run_dir="/r/c", decisions_exists=True, idle_seconds=60),
            "active", False,
        ),
        (
            "idle with no game is REPORTED, never reaped",
            Observation(port="4", run_dir="/r/d", decisions_exists=True,
                        idle_seconds=threshold + 1),
            "idle-no-game", False,
        ),
        (
            "a game for a DIFFERENT run does not make this one live",
            Observation(port="5", run_dir="/r/e", decisions_exists=False,
                        game_run_dirs=("/r/other",)),
            "orphan-data-gone", True,
        ),
        (
            "no decision log in the environment is unknown, never reaped",
            Observation(port="6", run_dir=None),
            "unknown", False,
        ),
        (
            "an unreadable mtime is unknown, never reaped",
            Observation(port="7", run_dir="/r/f", decisions_exists=True, idle_seconds=None),
            "unknown", False,
        ),
    ]
    failures = 0

    # The launch wrapper's command line arrives shell-quoted, and a port parsed as
    # '"8088"' invents a phantom orchestrator AND splits the real group — measured here.
    # A split group is the dangerous half: reaping would signal some of a run and leave
    # the rest serving. These cases exist so that cannot regress silently.
    for raw, want_port in [
        ('uv run neural-amplifier serve --port 8024', "8024"),
        ('sh -cu ... uv run ... serve --port "8088" > log', "8088"),
        ("serve --port=8097", "8097"),
        ("serve --port abc", ""),
        ("serve with no port at all", ""),
    ]:
        got_port = _port_of(raw)
        ok = got_port == want_port
        print(f"  {'PASS' if ok else 'FAIL'}  port parse {raw[:44]!r} -> {got_port!r}")
        if not ok:
            failures += 1

    for name, obs, want_state, want_reapable in cases:
        got = classify(obs, threshold)
        ok = got.state == want_state and got.reapable == want_reapable
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(
                f"        want {want_state}/reapable={want_reapable}, "
                f"got {got.state}/{got.reapable}"
            )
            failures += 1
    reapable = [c for c in cases if c[3]]
    print(
        f"\n  {len(cases)} classifier cases, {len(reapable)} auto-reapable, "
        f"{failures} failure(s) overall"
    )
    if failures == 0:
        print("  discrimination proven: only 'orphan-data-gone' is ever reapable")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--reap", action="store_true",
                        help="actually stop unambiguous orphans (default: report only)")
    parser.add_argument("--idle-minutes", type=int, default=DEFAULT_IDLE_MINUTES,
                        help="idle time before an orchestrator is REPORTED (never reaped)")
    parser.add_argument("--selftest", action="store_true",
                        help="prove the classifier discriminates, without touching the host")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    threshold = args.idle_minutes * 60
    rows = []
    for obs in observe():
        verdict = classify(obs, threshold)
        action = ""
        if verdict.reapable:
            action = reap(obs, dry_run=not args.reap)
        rows.append((obs, verdict, action))

    if args.json:
        print(json.dumps([
            {"port": o.port, "pids": o.pids, "run_dir": o.run_dir, "state": v.state,
             "why": v.why, "reapable": v.reapable, "action": a, "uptime_s": o.uptime_seconds}
            for o, v, a in rows
        ], indent=2))
        return 0

    banner = "" if args.reap else "   [report only — pass --reap to act]"
    print(f"ORCHESTRATORS ({len(rows)}){banner}")
    for obs, verdict, action in rows:
        print(
            f"  port {obs.port:<6} up {obs.uptime_seconds // 3600:>3}h  "
            f"{verdict.state:<18} {verdict.why}"
        )
        if action:
            print(f"      -> {action}")

    copies = tmpfs_run_copies()
    if copies:
        print("\nRUN COPIES ON tmpfs (this is RAM). NEVER deleted by this tool — MOVE them:")
        for path, size in copies[:10]:
            print(f"  {size / 1e9:6.1f} GB  {path}")
        print(f"\n  suggested: mv <path> {os.path.expanduser('~/workspace/na-runs')}/<name>")
        print(
            "  one of these was the sole copy of a published result's raw data"
            " — look before moving."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
