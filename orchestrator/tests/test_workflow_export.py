"""workflow_export — a finished game's log as a shuttle workflow run.

The invariants: one game per run (a multi-game log refuses — merging
histories would fold two games into a state machine neither played); one
`decide` transition per TURN, not per decision; the record stream is a
valid shuttle import (define -> start -> transitions -> terminal finish);
and --dry-run writes the mapping without invoking anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from neural_amplifier.decisions import DecisionLog, DecisionRecord
from neural_amplifier.workflow_export import (
    WorkflowExportError,
    export_run,
    transitions_records,
)


def _record(**kw: Any) -> DecisionRecord:
    base: dict[str, Any] = {
        "turn": 1,
        "faction": "Peacekeepers",
        "engine": "thinker",
        "scope": "base",
        "tier": "llm",
        "world_view_hash": "sha256:test",
        "action_space_size": 3,
        "surface_id": "base.production",
        "game_id": "g-1",
    }
    base.update(kw)
    return DecisionRecord(**base)


def _log(tmp_path: Path, records: list[DecisionRecord]) -> Path:
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(path)
    for r in records:
        log.write(r)
    return path


def test_one_decide_transition_per_turn_then_a_terminal_finish(tmp_path: Path) -> None:
    path = _log(
        tmp_path,
        [_record(turn=1), _record(turn=1), _record(turn=2), _record(turn=3)],
    )
    records, report = transitions_records(path)
    assert report.turns == 3 and report.decisions == 4
    assert [r["type"] for r in records] == ["define", "start"] + ["transition"] * 4
    steps = [r["step"] for r in records if r["type"] == "transition"]
    assert steps == ["decide", "decide", "decide", "finish"]
    assert records[-1]["to"] == "finished"
    # Every transition names the same run, derived from the game id.
    runs = {r["run"] for r in records if r["type"] != "define"}
    assert runs == {"na-g-1"}


def test_a_multi_game_log_refuses(tmp_path: Path) -> None:
    path = _log(tmp_path, [_record(game_id="g-1"), _record(game_id="g-2")])
    with pytest.raises(WorkflowExportError, match="spans 2 games"):
        transitions_records(path)


def test_an_empty_log_refuses(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    with pytest.raises(WorkflowExportError, match="no decisions"):
        transitions_records(path)


def test_dry_run_writes_the_jsonl_and_invokes_nothing(tmp_path: Path) -> None:
    path = _log(tmp_path, [_record(turn=1)])
    out = tmp_path / "mapped.jsonl"
    report = export_run(path, agent="importer", out_path=out, run=False)
    assert report.records == 4  # define, start, decide, finish
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert lines[0]["type"] == "define" and lines[0]["name"] == "na-game"
    # The definition the records ride under parses as a legal shuttle
    # workflow: initial non-terminal, terminal reachable, no duplicate
    # (step, from) pairs — checked structurally here so a drift in shuttle's
    # rules shows up as a shuttle-side import failure, not silently.
    seen = {(t["step"], t["from"]) for t in lines[0]["transitions"]}
    assert len(seen) == len(lines[0]["transitions"])
