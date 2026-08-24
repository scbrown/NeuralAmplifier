"""Tests for scripts/save_census.py — the all-faction census read out of a saved game.

The point of each test is the REFUSAL. A census that silently reports the wrong run, or reports
a number from a file it could not really parse, is worse than no census: the A/B it feeds has no
other way to notice.
"""

from __future__ import annotations

import importlib.util
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


def save_bytes(bases: list[tuple[str, int]], lead: int = 64) -> bytes:
    """A synthetic save holding exactly ``bases`` as (name, faction) in table order."""
    out = bytearray(census_mod.MAGIC + b"\x00" * lead)
    for name, faction in bases:
        start = len(out)
        record = bytearray(census_mod.STRIDE)
        encoded = name.encode("latin1")
        record[: len(encoded)] = encoded
        out += record
        # The owning faction sits BEFORE the name, so it lands inside the previous record (or,
        # for the first base, in the padding ahead of the table). Writing it after the record is
        # appended is what keeps the two in the right relationship.
        out[start + census_mod.FACTION_OFFSET] = faction
    return bytes(out)


def write(tmp_path: Path, name: str, data: bytes, mtime: float) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    import os

    os.utime(path, (mtime, mtime))
    return path


ONE_EACH = [(f"Base {f}", f) for f in range(1, 8)]


def test_counts_every_faction(tmp_path):
    path = write(tmp_path, "Autosave_2101.sav", save_bytes(ONE_EACH), 1000)
    row = census_mod.census(path)
    assert row["total_bases"] == 7
    assert row["bases"] == {str(f): 1 for f in range(1, 8)}
    assert row["turn"] == 1


def test_finds_the_table_wherever_it_sits(tmp_path):
    """The table MOVES between saves of one game. Anchoring on a fixed offset would be wrong."""
    near = census_mod.census(write(tmp_path, "a.sav", save_bytes(ONE_EACH, lead=64), 1000))
    far = census_mod.census(write(tmp_path, "b.sav", save_bytes(ONE_EACH, lead=9000), 1000))
    assert near["bases"] == far["bases"]


def test_second_base_shows_up_under_its_owner(tmp_path):
    data = save_bytes(ONE_EACH + [("Second Seven", 7)])
    row = census_mod.census(write(tmp_path, "Autosave_2110.sav", data, 1000))
    assert row["bases"]["7"] == 2
    assert row["names"]["7"] == ["Base 7", "Second Seven"]
    assert row["turn"] == 10


def test_refuses_a_file_that_is_not_a_save(tmp_path):
    path = write(tmp_path, "Autosave_2101.sav", b"NOTASAVE" + b"\x00" * 400, 1000)
    with pytest.raises(ValueError, match="not a saved game"):
        census_mod.census(path)


def test_refuses_a_save_with_no_base_table(tmp_path):
    path = write(tmp_path, "Autosave_2101.sav", census_mod.MAGIC + b"\x00" * 4000, 1000)
    with pytest.raises(ValueError, match="no base table"):
        census_mod.census(path)


def test_older_saves_are_refused_not_censused(tmp_path, capsys):
    """The whole point of --since. saves/auto is keyed by game YEAR, not by run."""
    write(tmp_path, "Autosave_2150.sav", save_bytes(ONE_EACH), 500)   # an earlier run
    write(tmp_path, "Autosave_2101.sav", save_bytes(ONE_EACH), 1500)  # this run
    argv = [sys.argv[0], str(tmp_path), "--since", "1000"]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sys, "argv", argv)
    assert census_mod.main() == 0
    out = capsys.readouterr()
    assert "refused 1 save(s) as belonging to an earlier run" in out.err
    assert "turn  150" not in out.out
    assert "turn    1" in out.out
    monkey.undo()


def test_everything_stale_is_a_refusal_not_an_empty_census(tmp_path, capsys):
    write(tmp_path, "Autosave_2150.sav", save_bytes(ONE_EACH), 500)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sys, "argv", [sys.argv[0], str(tmp_path), "--since", "1000"])
    assert census_mod.main() == 2
    assert "nothing to census" in capsys.readouterr().err
    monkey.undo()


def test_series_reports_a_loss_and_does_not_guess_the_cause(capsys):
    rows = [
        {"turn": 1, "bases": {"7": 2}},
        {"turn": 2, "bases": {"7": 3}},
        {"turn": 3, "bases": {"7": 2}},
    ]
    assert census_mod.report_series(rows, 7) == 1
    out = capsys.readouterr().out
    assert "DECREASED at turn(s) [3]" in out
    assert "abandoned, captured or starved" in out


def test_series_clears_a_run_that_never_lost_a_base(capsys):
    rows = [{"turn": t, "bases": {"7": v}} for t, v in [(1, 1), (2, 2), (3, 2), (4, 3)]]
    assert census_mod.report_series(rows, 7) == 0
    assert "never decreased" in capsys.readouterr().out
