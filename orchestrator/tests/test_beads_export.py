"""The export guard has to be able to refuse — na-2a9.

`scripts/beads-export.py` exists because `bd`'s auto-export on write is gated: a
second write shortly after the first silently does not export, `bd` exits 0, and
the git-tracked tracker falls behind the store. A stale export is a well-formed
file with the right id count and the wrong field values, so it reverts closed
beads for everyone who pulls and the commit looks completely normal. It has cost
two hand repairs on this repo (36d67d0, 3a0158a) and bit twice more in one
session.

The guard was written and wired into `just beads-export`. It had no tests. A
guard nobody has watched refuse is a guard you are trusting on its comment, and
this one's whole value is in the refusing — every check below is a case where
the guard must say no and stop the write.

`regressions()` is pure, so these drive it directly rather than standing up a
Dolt store. The one thing that genuinely needs `bd` — that a forced `bd export
-o` returns fresher data than the auto path — is the measurement recorded on the
bead, not something a unit test can restate.
"""

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


# --------------------------------------------------------------- must refuse


def test_a_close_that_uncloses_is_refused() -> None:
    """The signature of both prior incidents, and the reason the guard exists."""
    before = _indexed(_issue("na-1p2", status="closed"))
    after = _indexed(_issue("na-1p2", status="in_progress"))

    problems = _module().regressions(before, after)

    assert len(problems) == 1
    assert "na-1p2" in problems[0]
    assert "closed" in problems[0]


def test_a_disappearing_issue_is_refused() -> None:
    before = _indexed(_issue("na-1p2"), _issue("na-2a9"))
    after = _indexed(_issue("na-1p2"))

    problems = _module().regressions(before, after)

    assert len(problems) == 1
    assert "na-2a9" in problems[0]


def test_shrinking_notes_are_refused() -> None:
    """`bd update --notes` REPLACES. That destroyed a 1510-char audit this session."""
    before = _indexed(_issue("na-lnv", notes="x" * 1510))
    after = _indexed(_issue("na-lnv", notes="short"))

    problems = _module().regressions(before, after)

    assert len(problems) == 1
    assert "--append-notes" in problems[0]


def test_disappearing_comments_are_refused() -> None:
    before = _indexed(_issue("na-2a9", comments=[{"body": "a"}, {"body": "b"}]))
    after = _indexed(_issue("na-2a9", comments=[{"body": "a"}]))

    problems = _module().regressions(before, after)

    assert len(problems) == 1
    assert "2 -> 1" in problems[0]


def test_every_regression_in_one_export_is_reported_not_just_the_first() -> None:
    """A stale export loses many records at once; fixing them one run at a time
    is how a repair session turns into four."""
    before = _indexed(
        _issue("na-aaa", status="closed"),
        _issue("na-bbb", notes="y" * 100),
        _issue("na-ccc"),
    )
    after = _indexed(
        _issue("na-aaa", status="open"),
        _issue("na-bbb", notes=""),
    )

    problems = _module().regressions(before, after)

    assert len(problems) == 3
    assert {"na-aaa", "na-bbb", "na-ccc"} == {p.split(":")[0] for p in problems}


# --------------------------------------------------------------- must allow


def test_normal_forward_progress_is_not_a_regression() -> None:
    """The guard must not block the writes it exists to protect.

    A guard that refuses everything is as useless as one that refuses nothing,
    and far more likely to be switched off.
    """
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
    """No committed JSONL yet — the first export cannot be losing work."""
    after = _indexed(_issue("na-1p2", status="closed"))

    assert _module().regressions({}, after) == []


def test_a_reopen_is_refused_even_though_it_is_legitimate() -> None:
    """Deliberate, and worth pinning so nobody 'fixes' it.

    A human reopening a bead is a real action, and the guard blocks it. That is
    the correct trade: a reopen is rare and its author is present to override,
    while a silent un-close is common and has nobody watching. The guard's
    message says so ('a reopen is a human action').
    """
    before = _indexed(_issue("na-1p2", status="closed"))
    after = _indexed(_issue("na-1p2", status="open"))

    problems = _module().regressions(before, after)

    assert len(problems) == 1
    assert "human action" in problems[0]


def test_missing_notes_and_comments_keys_are_treated_as_empty() -> None:
    """Real bd records omit these rather than sending null, and a crash in the
    guard is a guard that gets bypassed."""
    before = _indexed({"id": "na-1p2", "status": "open"})
    after = _indexed({"id": "na-1p2", "status": "open"})

    assert _module().regressions(before, after) == []
