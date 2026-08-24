"""workflow_export — a finished game's decision log as a shuttle workflow run.

One invariant, module-map style: **a finished game is exportable as a
workflow run.** The decision log (the record of truth for a run,
``decisions.DecisionLog``) maps onto shuttle's model — the workflow engine
of the quipu stack (github.com/scbrown/shuttle) — as a transitions JSONL
that ``shuttle import-run`` ingests and re-signs. From there the run lives
in quipu's windowed operational graphs beside every other crew's runs,
freezable when its window completes.

Deliberately OFF the game path: this is a CLI subcommand over a finished
log, so invariant #9 (degrade safely — the game never stalls) is satisfied
structurally rather than defensively. It writes no quipu itself — shuttle
owns that seam, including the capability probe and the window discipline.

Two honesty notes, stated here because the mapping could quietly lie:

- The decision log carries NO wall clock (deliberately — turns are game
  time). The transition ``at`` timestamps are therefore the EXPORT moment,
  monotonically nudged per transition: they record when the run was
  attested into shuttle, not when the turns were played. The turn number
  rides in the step's per-turn granularity instead.
- The importing agent signs every transition (``shuttle import-run
  --agent``): the signature attests the MAPPING, not the original play —
  the game engine never signed shuttle messages, and pretending otherwise
  would forge exactly what the signature exists to prove.

Config: ``[shuttle] bin`` in ``na.toml`` or ``NA_SHUTTLE_BIN`` (env wins,
the file's own precedence rule), default ``shuttle`` on PATH.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_PATH
from .decisions import DecisionLog

#: The workflow definition every exported game runs under. A game is
#: "playing" until it is "finished"; each played TURN is one `decide`
#: self-loop transition, so run length is turns, not decisions.
DEFINITION = {
    "type": "define",
    "name": "na-game",
    "initial": "playing",
    "terminal": ["finished"],
    "transitions": [
        {"step": "decide", "from": "playing", "to": "playing"},
        {"step": "finish", "from": "playing", "to": "finished"},
    ],
}


class WorkflowExportError(RuntimeError):
    """The export could not proceed. Raised to the CLI, never swallowed."""


def shuttle_bin() -> str:
    """``NA_SHUTTLE_BIN`` > ``[shuttle] bin`` in na.toml > ``shuttle``."""
    env = os.environ.get("NA_SHUTTLE_BIN")
    if env:
        return env
    path = Path(os.environ.get("NA_CONFIG") or DEFAULT_PATH)
    if path.exists():
        try:
            table = tomllib.loads(path.read_text()).get("shuttle", {})
        except tomllib.TOMLDecodeError:
            table = {}
        if isinstance(table, dict) and isinstance(table.get("bin"), str):
            return table["bin"]
    return "shuttle"


@dataclass(frozen=True)
class ExportReport:
    """What the mapping produced, before any subprocess runs."""

    game_id: str
    turns: int
    decisions: int
    records: int


def transitions_records(log_path: Path) -> tuple[list[dict], ExportReport]:
    """Map a finished decision log to shuttle import-run records.

    One ``decide`` transition per PLAYED TURN (not per decision — a turn can
    hold dozens), then the terminal ``finish``. Refuses an empty log and a
    log spanning multiple games — one run is one game, and merging two would
    fold their histories into a state machine neither played.
    """
    records = list(DecisionLog(log_path).read())
    if not records:
        raise WorkflowExportError(f"{log_path} holds no decisions")
    games = {r.game_id for r in records}
    if len(games) != 1:
        raise WorkflowExportError(
            f"{log_path} spans {len(games)} games ({sorted(games)}); export "
            "one game per run — merging histories would fold two games into "
            "a state machine neither played"
        )
    game_id = games.pop() or "unknown-game"
    turns = sorted({r.turn for r in records})

    base = datetime.datetime.now(datetime.UTC).replace(microsecond=0)

    def at(i: int) -> str:
        return (base + datetime.timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ")

    run_id = f"na-{game_id}"
    out: list[dict] = [dict(DEFINITION, at=at(0))]
    out.append(
        {
            "type": "start",
            "run": run_id,
            "definition": "na-game",
            "state": "playing",
            "at": at(0),
        }
    )
    for i, _turn in enumerate(turns):
        out.append(
            {
                "type": "transition",
                "run": run_id,
                "definition": "na-game",
                "window": at(0)[:7],
                "step": "decide",
                "from": "playing",
                "to": "playing",
                "at": at(i + 1),
            }
        )
    out.append(
        {
            "type": "transition",
            "run": run_id,
            "definition": "na-game",
            "window": at(0)[:7],
            "step": "finish",
            "from": "playing",
            "to": "finished",
            "at": at(len(turns) + 1),
        }
    )
    report = ExportReport(
        game_id=game_id, turns=len(turns), decisions=len(records), records=len(out)
    )
    return out, report


def export_run(
    log_path: Path, agent: str, out_path: Path | None = None, run: bool = True
) -> ExportReport:
    """Write the transitions JSONL and (optionally) hand it to shuttle.

    ``run=False`` (the CLI's ``--dry-run``) writes the file and stops —
    what WOULD be imported, inspectable. The subprocess's own refusals
    (unknown workflow states, a run that already exists) surface verbatim;
    this module adds nothing on top of shuttle's error discipline.
    """
    records, report = transitions_records(log_path)
    out_path = out_path or log_path.with_suffix(".shuttle.jsonl")
    out_path.write_text("".join(json.dumps(r) + "\n" for r in records))
    if run:
        cmd = [shuttle_bin(), "import-run", str(out_path), "--agent", agent]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise WorkflowExportError(
                f"`{' '.join(cmd)}` failed ({proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
    return report
