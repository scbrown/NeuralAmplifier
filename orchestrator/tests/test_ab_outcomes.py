"""The A/B trajectory comparison, and the confound it refuses to let you have — na-6db.

Producing two runs needs a game. Reading them does not, which is why this half exists now: when
someone has a game, the analysis is already written and already argues with them.

The tests are mostly about REFUSALS, because that is where the value is. A trajectory
comparison is easy to produce and easy to produce *wrongly*, and every wrong version yields a
clean number with a confident sign — which is exactly what a reader will quote.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "ab_outcomes", REPO / "scripts" / "ab_outcomes.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ab = _module()


def arm(
    path: Path,
    tier: str,
    *,
    difficulty: str = "librarian",
    slot: str = "human",
    turns: int = 40,
    growth: int = 1,
) -> Path:
    with path.open("w") as handle:
        for turn in range(1, turns + 1):
            handle.write(
                json.dumps(
                    {
                        "surface_id": "base.production",
                        "turn": turn,
                        "tier": tier,
                        "fairness": {"slot": slot, "difficulty": difficulty, "handicaps": []},
                        "metrics": {
                            "base_count": 2 + turn // 10 * growth,
                            "mineral_surplus": 2 + turn // 8 * growth,
                            "energy_reserves": 80 + turn,
                        },
                    }
                )
                + "\n"
            )
    return path


def run(module: Any, baseline: Path, brain: Path, *extra: str) -> int:
    import sys

    argv = sys.argv
    sys.argv = ["ab_outcomes", str(baseline), str(brain), *extra]
    try:
        return module.main()
    finally:
        sys.argv = argv


def test_a_valid_comparison_reports_the_games_own_measures(tmp_path, capsys) -> None:
    """The metrics trended are the ones the GAME keeps, not ones we invented.

    A comparison on a measure of our own choosing is one we can accidentally rig — pick the
    metric the brain happens to optimise and every result is a win.
    """
    baseline = arm(tmp_path / "b.jsonl", "deterministic")
    brain = arm(tmp_path / "m.jsonl", "llm", growth=2)
    assert run(ab, baseline, brain) == 0
    out = capsys.readouterr().out
    assert "base_count" in out and "mineral_surplus" in out
    assert "No verdict" in out, "a script that announced a winner would be read as one"


def test_it_refuses_arms_with_different_fairness_profiles(tmp_path) -> None:
    """The confound na-6db names, enforced rather than remembered.

    An AI slot inherits difficulty handicaps a human slot does not (invariant 6). Comparing
    across a difficulty gap measures the handicap and not the brain — and produces a clean
    number with a confident sign, which is the dangerous kind of wrong.

    A refusal rather than a caveat, because a caveat is something a reader can skip.
    """
    baseline = arm(tmp_path / "b.jsonl", "deterministic")
    easier = arm(tmp_path / "m.jsonl", "llm", difficulty="citizen")
    assert run(ab, baseline, easier) == 1


def test_a_baseline_containing_llm_decisions_is_not_a_baseline(tmp_path) -> None:
    """Cheap to check, and it invalidates everything downstream if missed."""
    brain = arm(tmp_path / "m.jsonl", "llm")
    assert run(ab, brain, brain) == 1


def test_a_brain_arm_with_no_llm_decisions_is_refused(tmp_path) -> None:
    """Two engine arms compared against each other report run-to-run noise as a result — the
    most convincing wrong answer this tool could give, since the shape looks right."""
    baseline = arm(tmp_path / "b.jsonl", "deterministic")
    assert run(ab, baseline, baseline) == 1


def test_a_short_run_is_refused(tmp_path) -> None:
    """na-6db's acceptance says at least 30 turns. A shorter run measures the opening, which
    is largely scripted, rather than the decisions."""
    baseline = arm(tmp_path / "b.jsonl", "deterministic")
    short = arm(tmp_path / "m.jsonl", "llm", turns=10)
    assert run(ab, baseline, short) == 1


def test_the_profile_key_ignores_handicaps_somebody_chose(tmp_path) -> None:
    """A handicap someone selected is a legitimate difference between arms; one the engine
    imposed is the confound. Only `structural` entries are part of the key."""
    chosen = {
        "slot": "ai",
        "difficulty": "thinker",
        "handicaps": [{"name": "free_units", "selected_by": "player"}],
    }
    plain = {"slot": "ai", "difficulty": "thinker", "handicaps": []}
    assert ab.profile_key(chosen) == ab.profile_key(plain)

    imposed = {
        "slot": "ai",
        "difficulty": "thinker",
        "handicaps": [{"name": "cheap_tech", "selected_by": "structural"}],
    }
    assert ab.profile_key(imposed) != ab.profile_key(plain)
