"""The harvest's two sinks — `scripts/harvest-world-views.py`, na-0oa.

The script exists because a manual harvest is a judgement call made at the end of a long
session, and it failed twice. Then it acquired the same shape of bug itself: a world view can
land in the `NA_WORLD_VIEW_STORE` directory *or* in the adapter's own `na-observations.jsonl`,
which lives in the SMAC install, and the script read the first only. `base.hurry` sat in a play
directory's log for a day while two sweeps concluded no capture survived anywhere on disk.

That is worse than an ordinary gap, and the bead says so: the script's existence is what makes
people stop looking by hand, so the sink it does not watch is *less* likely to be searched than
before the script existed.

So the test the bead demands is by mechanism rather than by exit status — delete a surface from
the store, leave it in the log, and require the harvest to still find it. A harvest that cannot
demonstrate finding a log-only surface has not been fixed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "harvest-world-views.py"


def _load_script() -> Any:
    """Import a hyphenated, non-package script by path."""
    spec = importlib.util.spec_from_file_location("harvest_world_views", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load_script()


def view(surface: str, turn: int = 42, **extra: object) -> dict:
    """A minimally valid contract world view — the four required fields plus a surface id."""
    return {
        "engine": "thinker",
        "scope": "base",
        "turn": turn,
        "faction": "GAIANS",
        "surface_id": surface,
        **extra,
    }


def write_store(root: Path, views: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i, payload in enumerate(views):
        (root / f"sha256-{i:02d}.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def write_log(path: Path, lines: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


# --- the gap this closes ---------------------------------------------------


def test_a_surface_only_the_adapter_log_has_is_still_found(tmp_path: Path) -> None:
    """The na-0oa mechanism, verbatim: absent from the store, present in the log."""
    store = write_store(tmp_path / "views", [view("base.production")])
    log = write_log(tmp_path / "play" / harvest.LOG_NAME, [view("base.hurry")])

    found = harvest.richest(harvest.from_store(store) + harvest.from_log(log))

    assert set(found) == {"base.production", "base.hurry"}
    assert found["base.hurry"][2] == "log"
    assert found["base.production"][2] == "store"


def test_the_play_directory_is_accepted_in_place_of_the_file(tmp_path: Path) -> None:
    """Nobody remembers whether the log is named or the directory is. Both work."""
    play = tmp_path / "play"
    write_log(play / harvest.LOG_NAME, [view("base.hurry")])

    assert harvest.resolve_log(play) == play / harvest.LOG_NAME
    assert harvest.resolve_log(play / harvest.LOG_NAME) == play / harvest.LOG_NAME
    assert harvest.resolve_log(tmp_path / "nowhere") is None


def test_each_capture_reports_which_sink_it_came_from(tmp_path: Path) -> None:
    """ "The store had it" and "only the log had it" are different facts about a run, and
    collapsing them would hide the very gap this fix closes."""
    store = write_store(tmp_path / "views", [view("base.production", history=["a"] * 40)])
    log = write_log(tmp_path / harvest.LOG_NAME, [view("base.production")])

    found = harvest.richest(harvest.from_store(store) + harvest.from_log(log))
    assert found["base.production"][2] == "store"  # the fuller one, and it says so


def test_the_richest_capture_wins_across_sinks_not_within_one(tmp_path: Path) -> None:
    """One rule spanning both sources rather than a preference for either. Where a surface
    reached both, the fuller capture is the one worth committing whichever door it came
    through — the log's copy is not second-class for being in the log."""
    store = write_store(tmp_path / "views", [view("base.hurry")])
    log = write_log(tmp_path / harvest.LOG_NAME, [view("base.hurry", economy={"x": [0] * 50})])

    found = harvest.richest(harvest.from_store(store) + harvest.from_log(log))
    assert found["base.hurry"][2] == "log"


# --- what must not be mistaken for a capture -------------------------------


def test_a_divergence_record_is_not_a_world_view(tmp_path: Path) -> None:
    """The log mixes full world views with the compact records `na_verify_*` emits. A
    divergence carries a `surface_id` and none of the contract's required fields; without the
    check it would be picked for any surface it was the only line for and written out as a
    fixture that never was a world view."""
    log = write_log(
        tmp_path / harvest.LOG_NAME,
        [
            {
                "surface_id": "base.production",
                "event": "divergence",
                "turn": 42,
                "base_id": 0,
                "intended_item_name": "Recycling Tanks",
                "applied_item_name": "Scout Patrol",
            }
        ],
    )
    assert harvest.from_log(log) == []


def test_a_truncated_final_line_costs_that_line_and_no_other(tmp_path: Path) -> None:
    """The log is appended as the game runs, so a run killed mid-write leaves a partial line.
    Raising would cost every capture in the file, which is the tradeoff this script exists to
    refuse."""
    path = tmp_path / harvest.LOG_NAME
    path.write_text(
        json.dumps(view("base.hurry")) + "\n" + json.dumps(view("faction.tech"))[:40],
        encoding="utf-8",
    )
    assert [c[0] for c in harvest.from_log(path)] == ["base.hurry"]


def test_a_capture_whose_schema_drifted_is_still_harvested(tmp_path: Path) -> None:
    """Checked structurally rather than by importing the model. An expensive capture taken
    before a field existed is still the only copy of that surface, and refusing it would
    destroy it to enforce a schema."""
    log = write_log(tmp_path / harvest.LOG_NAME, [view("base.hurry", invented_later="whatever")])
    assert [c[0] for c in harvest.from_log(log)] == ["base.hurry"]


# --- end to end ------------------------------------------------------------


def test_the_harvest_runs_over_both_sinks_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run naming a store and finding the log through SMAC_PLAY_DIR, which is what a real
    session has set. Asserted on the report rather than on the exit status: a harvest that exits
    0 having read one sink is exactly the failure this closes."""
    store = write_store(tmp_path / "views", [view("base.production")])
    write_log(tmp_path / "play" / harvest.LOG_NAME, [view("base.hurry", turn=35)])

    monkeypatch.setenv("SMAC_PLAY_DIR", str(tmp_path / "play"))
    monkeypatch.setattr("sys.argv", ["harvest-world-views.py", str(store)])

    assert harvest.main() == 0

    out = capsys.readouterr().out
    assert "base.hurry" in out
    assert "log" in out
    assert "dry run" in out  # nothing was written


def test_naming_no_source_at_all_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silence is the one answer this script must never give — reporting "no world views" for a
    harvest that read nothing is how the original failure looked from the outside."""
    monkeypatch.delenv("SMAC_PLAY_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["harvest-world-views.py"])

    assert harvest.main() == 2
    assert "nothing to harvest" in capsys.readouterr().err
