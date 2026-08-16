"""Per-directive attention and override rates — na-mmp's analysis half.

`just coverage` reports adherence in aggregate. na-mmp wants it per directive, because the two
failures it names are invisible in an average: a high-priority directive overridden every time
(mispriced) is diluted by every directive that was followed, and one never overridden looks like
success while being equally consistent with a directive that never constrained anything.

The rates need a real run. Reading a log does not, so this half is written and tested now.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "directive_report", REPO / "scripts" / "directive_report.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dr = _module()


def log(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def decision(turn: int, **plan: list[str]) -> dict:
    block = {
        "in_force": [],
        "followed": [],
        "overrode": [],
        "unmeasurable": [],
        "unsatisfied": [],
    }
    block.update(plan)
    return {"turn": turn, "plan": block}


def test_unmeasurable_is_an_adapter_gap_not_a_directive_failure(tmp_path) -> None:
    """The distinction the whole report is built around.

    `unmeasurable` means the world view did not report the directive's metric — a missing field
    in the adapter. Folding it in with "checked and failing" would turn a bug in one repository
    into an apparently unhelpful directive in another, and send whoever reads the number to fix
    the wrong thing.

    So it is excluded from the denominator: a directive is not charged for decisions it could
    never have been checked on.
    """
    rows = [decision(t, in_force=["d"], unmeasurable=["d"]) for t in range(1, 26)]
    tallies, _ = dr.tally(log(tmp_path / "a.jsonl", rows))
    entry = tallies["d"]
    assert entry.in_force == 25
    assert entry.unmeasurable == 25
    assert entry.measurable == 0
    assert entry.attention is None, "an unmeasurable directive has no attention rate, not 0.0"
    assert entry.override is None


def test_rates_use_only_measurable_decisions(tmp_path) -> None:
    """Half checkable, half not: the rate is over the half that could be checked."""
    rows = [decision(t, in_force=["d"], followed=["d"]) for t in range(1, 11)]
    rows += [decision(t, in_force=["d"], unmeasurable=["d"]) for t in range(11, 21)]
    tallies, _ = dr.tally(log(tmp_path / "a.jsonl", rows))
    entry = tallies["d"]
    assert entry.measurable == 10
    assert entry.attention == 1.0, "10 followed of 10 checkable, not of 20 in force"


def test_a_short_log_is_refused(tmp_path) -> None:
    """na-mmp's premise: every measurement so far is ten replays of ONE captured observation
    with a hand-written plan. That shows the mechanism works and says nothing about whether
    directives help — so a rate over a handful of decisions is the exact thing not to report."""
    import sys

    rows = [decision(t, in_force=["d"], followed=["d"]) for t in range(1, 6)]
    path = log(tmp_path / "a.jsonl", rows)
    argv = sys.argv
    sys.argv = ["directive_report", str(path)]
    try:
        assert dr.main() == 1
    finally:
        sys.argv = argv


def test_a_run_with_no_directives_is_reported_not_crashed(tmp_path) -> None:
    """A run with no standing plan has no attention to measure. That is an outcome, not a
    failure of the tool, and the two should not look the same to a caller."""
    import sys

    path = log(tmp_path / "a.jsonl", [{"turn": 1}])
    argv = sys.argv
    sys.argv = ["directive_report", str(path)]
    try:
        assert dr.main() == 1
    finally:
        sys.argv = argv


def test_both_failure_modes_are_detectable(tmp_path) -> None:
    """The two na-mmp names, and they are opposite shapes in the same column.

    Overridden every time at high priority is mispriced. Never overridden at all is not
    obviously good — it is equally consistent with a directive that never bound. Neither is
    visible in an aggregate adherence figure.
    """
    rows = [
        decision(t, in_force=["hot", "cold"], overrode=["hot"], followed=["cold"])
        for t in range(1, 31)
    ]
    tallies, decisions = dr.tally(log(tmp_path / "a.jsonl", rows))
    assert decisions == 30
    assert tallies["hot"].override == 1.0
    assert tallies["cold"].override == 0.0
    assert tallies["cold"].attention == 1.0
