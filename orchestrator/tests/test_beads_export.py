"""The export guard has to be able to refuse — na-2a9."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "beads_export", REPO / "scripts" / "beads-export.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _issue(issue_id: str, **fields: Any) -> dict:
    record = {"id": issue_id, "status": "open", "notes": "", "comments": []}
    record.update(fields)
    return record


def _indexed(*records: dict) -> dict[str, dict]:
    return {r["id"]: r for r in records}


def test_a_close_that_uncloses_is_refused() -> None:
    before = _indexed(_issue("na-1p2", status="closed"))
    after = _indexed(_issue("na-1p2", status="in_progress"))
    problems = _module().regressions(before, after)
    assert len(problems) == 1
    assert "na-1p2" in problems[0]
    assert "closed" in problems[0]


def test_a_disappearing_issue_is_refused() -> None:
    problems = _module().regressions(
        _indexed(_issue("na-1p2"), _issue("na-2a9")), _indexed(_issue("na-1p2"))
    )
    assert len(problems) == 1
    assert "na-2a9" in problems[0]


def test_shrinking_notes_are_refused() -> None:
    problems = _module().regressions(
        _indexed(_issue("na-lnv", notes="x" * 1510)),
        _indexed(_issue("na-lnv", notes="short")),
    )
    assert len(problems) == 1
    assert "--append-notes" in problems[0]


def test_disappearing_comments_are_refused() -> None:
    problems = _module().regressions(
        _indexed(_issue("na-2a9", comments=[{"body": "a"}, {"body": "b"}])),
        _indexed(_issue("na-2a9", comments=[{"body": "a"}])),
    )
    assert len(problems) == 1
    assert "2 -> 1" in problems[0]


def test_every_regression_in_one_export_is_reported_not_just_the_first() -> None:
    before = _indexed(
        _issue("na-aaa", status="closed"),
        _issue("na-bbb", notes="y" * 100),
        _issue("na-ccc"),
    )
    after = _indexed(_issue("na-aaa", status="open"), _issue("na-bbb", notes=""))
    problems = _module().regressions(before, after)
    assert len(problems) == 3
    assert {"na-aaa", "na-bbb", "na-ccc"} == {p.split(":")[0] for p in problems}


def test_normal_forward_progress_is_not_a_regression() -> None:
    before = _indexed(_issue("na-1p2", status="open", notes="a"))
    after = _indexed(
        _issue("na-1p2", status="closed", notes="a plus more", comments=[{"body": "done"}]),
        _issue("na-new", status="open"),
    )
    assert _module().regressions(before, after) == []


def test_an_identical_export_is_not_a_regression() -> None:
    same = _indexed(_issue("na-1p2", status="closed", notes="n"))
    assert _module().regressions(same, dict(same)) == []


def test_an_empty_baseline_permits_everything() -> None:
    assert _module().regressions({}, _indexed(_issue("na-1p2", status="closed"))) == []


def test_a_reopen_is_refused_even_though_it_is_legitimate() -> None:
    problems = _module().regressions(
        _indexed(_issue("na-1p2", status="closed")),
        _indexed(_issue("na-1p2", status="open")),
    )
    assert len(problems) == 1
    assert "human action" in problems[0]


def test_missing_notes_and_comments_keys_are_treated_as_empty() -> None:
    before = _indexed({"id": "na-1p2", "status": "open"})
    after = _indexed({"id": "na-1p2", "status": "open"})
    assert _module().regressions(before, after) == []


def test_rotated_live_tail_appends_without_losing_snapshot() -> None:
    merged, problems = _module().merge_audit(
        {"old": {"id": "old", "kind": "created"}},
        {"new": {"id": "new", "kind": "closed"}},
    )
    assert problems == []
    assert list(merged) == ["old", "new"]


def test_changed_id_in_live_tail_is_refused() -> None:
    merged, problems = _module().merge_audit(
        {"same": {"id": "same", "kind": "created"}},
        {"same": {"id": "same", "kind": "closed"}},
    )
    assert merged["same"]["kind"] == "closed"
    assert problems == ["audit same: append-only record changed"]
