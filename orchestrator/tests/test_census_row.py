"""Tests for scripts/census_row.py — the join between the saves and the slope comparison.

Every test here is about a REFUSAL, because the failure this module exists to prevent is not a
crash: it is a row that looks complete, scores cleanly, and is quietly wrong.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


census_mod = load("save_census")
row_mod = load("census_row")

FAIRNESS = {"slot": "human", "difficulty": "talent", "handicaps": []}


def save_bytes(bases: list[tuple[str, int]], lead: int = 64) -> bytes:
    out = bytearray(census_mod.MAGIC + b"\x00" * lead)
    for name, faction in bases:
        start = len(out)
        record = bytearray(census_mod.STRIDE)
        encoded = name.encode("latin1")
        record[: len(encoded)] = encoded
        out += record
        out[start + census_mod.FACTION_OFFSET] = faction
    return bytes(out)


def make_run(root: Path, arm: str, counts: dict[int, int], *, started: float = 1000,
             manifest_extra: dict | None = None) -> Path:
    """A run directory whose faction-7 base count at turn T is counts[T]."""
    auto = root / "play" / "saves" / "auto"
    auto.mkdir(parents=True)
    for turn, ours in counts.items():
        bases = [(f"AI {f} base", f) for f in range(1, 7)]
        bases += [(f"Ours {i}", 7) for i in range(ours)]
        path = auto / f"Autosave_{2100 + turn}.sav"
        path.write_bytes(save_bytes(bases))
        os.utime(path, (started + 1, started + 1))
    manifest = {"seed": 1, "arm": arm, "faction": 7, "fairness": FAIRNESS,
                "census_turns": sorted(counts), "run_started_epoch": started}
    manifest.update(manifest_extra or {})
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def run_row(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "census_row.py"), str(root), *extra],
        capture_output=True, text=True,
    )


def test_emits_a_row_the_slope_scorer_can_read(tmp_path):
    root = make_run(tmp_path / "arm", "control", {50: 2, 55: 2, 80: 3})
    done = run_row(root)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    assert row["arm"] == "control"
    assert sorted(row["census"]) == ["50", "55", "80"]
    assert [row["census"][t]["7"] for t in ("50", "55", "80")] == [2, 2, 3]
    assert row["provenance"]["saves"]["80"] == "Autosave_2180.sav"


def test_refuses_a_missing_checkpoint_instead_of_back_filling(tmp_path):
    root = make_run(tmp_path / "arm", "control", {50: 2, 55: 2})
    done = run_row(root, "--checkpoints", "50,55,80")
    assert done.returncode == 1
    assert "no in-run save for turn(s) [80]" in done.stderr
    assert "quietly substitutes" in done.stderr
    assert done.stdout == ""


def test_refuses_a_save_from_an_earlier_run_under_the_same_name(tmp_path):
    """The trap: saves/auto is keyed by game YEAR, so an old game's turn 80 is already there."""
    root = make_run(tmp_path / "arm", "control", {50: 2, 55: 2})
    stale = root / "play" / "saves" / "auto" / "Autosave_2180.sav"
    stale.write_bytes(save_bytes([("Ours 1", 7)] + [(f"AI {f}", f) for f in range(1, 7)]))
    os.utime(stale, (500, 500))  # predates run_started_epoch
    done = run_row(root, "--checkpoints", "50,55,80")
    assert done.returncode == 1, "a parseable save from ANOTHER run must not fill a checkpoint"
    assert "no in-run save for turn(s) [80]" in done.stderr


def test_refuses_without_a_boundary(tmp_path):
    root = make_run(tmp_path / "arm", "control", {50: 2, 55: 2})
    manifest = json.loads((root / "manifest.json").read_text())
    del manifest["run_started_epoch"]
    (root / "manifest.json").write_text(json.dumps(manifest))
    done = run_row(root)
    assert done.returncode == 2
    assert "keyed by game year" in done.stderr


def test_refuses_without_a_manifest(tmp_path):
    (tmp_path / "bare").mkdir()
    done = run_row(tmp_path / "bare", "--checkpoints", "50,55")
    assert done.returncode == 2
    assert "not comparable to anything" in done.stderr


def test_the_row_scores_end_to_end(tmp_path):
    """The join is the point: two rows out of this, straight into expansion_slope, no typing."""
    control = make_run(tmp_path / "a", "control", {50: 2, 55: 2, 80: 2})
    compound = make_run(tmp_path / "b", "compound", {50: 5, 55: 5, 80: 7})
    results = tmp_path / "results.jsonl"
    with results.open("w") as fh:
        for root in (control, compound):
            done = run_row(root)
            assert done.returncode == 0, done.stderr
            fh.write(done.stdout)
    scored = subprocess.run(
        [sys.executable, str(SCRIPTS / "expansion_slope.py"), str(results), "--seed", "1",
         "--baseline", "control", "--compound", "compound", "--faction", "7",
         "--checkpoints", "50,55,80"],
        capture_output=True, text=True,
    )
    assert scored.returncode == 0, scored.stderr
    assert "control" in scored.stdout and "compound" in scored.stdout
    assert "No verdict" in scored.stdout


@pytest.mark.parametrize("checkpoints", ["50", "80,50", "50,50"])
def test_refuses_unusable_checkpoint_lists(tmp_path, checkpoints):
    root = make_run(tmp_path / "arm", "control", {50: 2, 80: 3})
    done = run_row(root, "--checkpoints", checkpoints)
    assert done.returncode == 2
    assert "ascending order" in done.stderr
