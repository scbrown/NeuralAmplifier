"""Does a decision's reasoning survive the turn that produced it? — aegis-n8zmq's baseline half.

The report's whole value is that its zeros mean something. A carry rate of 0 has to be a
measurement over a `tier` field that was present, and a rework rate that cannot be computed has
to say UNANSWERABLE rather than print 0.000 — that distinction is the one this codebase keeps
paying for, so it is what these tests hold.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "carry_report", REPO / "scripts" / "carry_report.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the module defines dataclasses, and `dataclasses` resolves a
    # field's type by looking its defining module up in `sys.modules`. A module loaded by path
    # alone is not there, and the failure is an AttributeError inside the stdlib rather than
    # anything that names the real cause.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cr = _module()


def log(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def observation(turn: int, base_id: int, item: str, tier: str = "llm") -> dict:
    return {
        "turn": turn,
        "base_id": base_id,
        "surface_id": "base.hurry",
        "tier": tier,
        "applied_item": item,
    }


def test_plan_tier_is_not_carry(tmp_path: Path) -> None:
    """A plan table is valid for exactly the turn it names, so it is bulk work and not carry.

    The failure this prevents is the most likely misreading of the whole report: bulk-turn mode
    answers 45 decisions in 0.04 s at `tier=plan`, and counting those would make a fast agent
    read as a strategic one.
    """
    path = log(
        tmp_path / "plan.jsonl",
        [observation(t, 1, "hurry:none", tier="plan") for t in range(1, 41)],
    )
    report = cr.read(path)
    assert report.decisions == 40
    assert report.carried == 0
    assert report.bulk == 40


def test_queued_and_deferred_carry(tmp_path: Path) -> None:
    rows = [observation(t, 1, "hurry:none", tier="queued") for t in range(1, 21)]
    rows += [observation(t, 2, "hurry:none", tier="deferred") for t in range(1, 21)]
    rows += [observation(t, 3, "hurry:none", tier="llm") for t in range(1, 21)]
    report = cr.read(log(tmp_path / "mixed.jsonl", rows))
    assert report.carried == 40
    assert report.decisions == 60


def test_same_turn_repeats_are_not_stability(tmp_path: Path) -> None:
    """The engine asks a base several times per turn and the adapter replays its cached answer.

    A pair inside one turn measures the cache, never the brain, so it must not enter the
    denominator — a run with many replays would otherwise report an impressively low rework rate
    earned entirely by the cache.
    """
    rows = [
        observation(1, 1, "hurry:none"),
        observation(1, 1, "hurry:none"),
        observation(1, 1, "hurry:none"),
        observation(2, 1, "hurry:now"),
    ]
    table = cr.rework(cr.read(log(tmp_path / "replay.jsonl", rows)))
    assert table[("base.hurry", "llm")] == (1, 1)


def test_rework_is_split_by_surface_and_tier(tmp_path: Path) -> None:
    """Never totalled: an average over a brain-driven surface and an engine-answered one moves
    when the surface mix does, which would make the headline respond to configuration."""
    rows = [observation(t, 1, "hurry:none" if t % 2 else "hurry:now") for t in range(1, 11)]
    rows += [
        {
            "turn": t,
            "base_id": 1,
            "surface_id": "base.defend_goal",
            "tier": "deterministic",
            "native_choice_item": "hold",
        }
        for t in range(1, 11)
    ]
    table = cr.rework(cr.read(log(tmp_path / "split.jsonl", rows)))
    assert table[("base.hurry", "llm")] == (9, 9)
    assert table[("base.defend_goal", "deterministic")] == (9, 0)


def test_engine_answered_records_still_count(tmp_path: Path) -> None:
    """`native_choice_item` is the deterministic tier's answer, and it is the only control the
    repository has for how often an answer *should* change on a given board."""
    rows = [
        {
            "turn": t,
            "base_id": 7,
            "surface_id": "base.defend_goal",
            "tier": "deterministic",
            "native_choice_item": "hold" if t < 5 else "advance",
        }
        for t in range(1, 11)
    ]
    report = cr.read(log(tmp_path / "native.jsonl", rows))
    assert report.no_answer == 0
    assert cr.rework(report)[("base.defend_goal", "deterministic")] == (9, 1)


def test_a_record_with_no_answer_is_counted_not_dropped(tmp_path: Path) -> None:
    """An excluded majority must not masquerade as a low rework rate."""
    rows = [{"turn": t, "base_id": 1, "surface_id": "base.workers", "tier": "llm"} for t in (1, 2)]
    report = cr.read(log(tmp_path / "silent.jsonl", rows))
    assert report.no_answer == 2
    assert cr.rework(report) == {}


def test_rework_is_unanswerable_without_base_id(tmp_path: Path) -> None:
    """Orchestrator decision records omit `base_id`, so two decisions on one base cannot be told
    from two on different ones. That has to read as unanswerable, never as 0.000."""
    rows = [
        {"turn": t, "surface_id": "base.hurry", "tier": "llm", "chosen": [{"action_id": "x"}]}
        for t in range(1, 41)
    ]
    report = cr.read(log(tmp_path / "orch.jsonl", rows))
    assert report.has_base_id is False
    assert cr.rework(report) == {}


def test_directive_age_needs_issued_turn(tmp_path: Path) -> None:
    """A log carrying the plan block's ids alone can say a directive was followed and cannot say
    how old the reasoning was. That gap is reported, not guessed at."""
    with_age = {
        "turn": 40,
        "surface_id": "base.hurry",
        "tier": "llm",
        "plan": {"in_force": ["d"], "followed": ["d"], "issued": []},
        "directives": [{"id": "d", "issued_turn": 12}],
    }
    without_age = {
        "turn": 41,
        "surface_id": "base.hurry",
        "tier": "llm",
        "plan": {"in_force": ["d"], "followed": ["d"], "issued": []},
    }
    report = cr.read(log(tmp_path / "ages.jsonl", [with_age, without_age]))
    assert report.directive_ages == [28]
    assert report.ages_unknown == 1
    assert report.followed_records == 2


def test_agent_issued_directives_are_counted(tmp_path: Path) -> None:
    """na-43h: the agent has never issued one. A run in which this stops being zero is the exit
    criterion for step 3 of the build order, so it needs its own counter."""
    rows = [
        {
            "turn": 1,
            "surface_id": "faction.tech",
            "tier": "llm",
            "plan": {"in_force": [], "followed": [], "issued": ["new-plan"]},
        }
    ]
    assert cr.read(log(tmp_path / "issued.jsonl", rows)).issued_by_agent == 1


def test_the_m1_baseline_still_reads_zero_carry() -> None:
    """The committed run this design was written against, kept as a regression on the claim.

    If a future change makes this non-zero, either something now carries — which is the goal —
    or the tier mapping drifted. Either way it should not happen silently.
    """
    report = cr.read(REPO / "evals" / "runs" / "na-6db" / "brain.faction7.jsonl")
    assert report.decisions == 1400
    assert report.carried == 0
    assert report.tiers["llm"] == 700
