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


def plan_record(
    turn: int,
    *,
    issued: list[str] | None = None,
    in_force: list[str] | None = None,
    followed: list[str] | None = None,
    overrode: list[str] | None = None,
    tier: str = "llm",
    game_id: str = "run-1",
) -> dict:
    return {
        "game_id": game_id,
        "turn": turn,
        "surface_id": "faction.strategy_review" if tier == "review" else "base.hurry",
        "tier": tier,
        "degraded": False,
        "plan": {
            "in_force": in_force or [],
            "followed": followed or [],
            "overrode": overrode or [],
            "issued": issued or [],
            "rejected": [],
        },
    }


def test_published_strategic_review_is_the_positive_control() -> None:
    report = cr.read(REPO / "evals" / "runs" / "aegis-n8zmq" / "carry-reviewed.json")
    funnel = report.funnel
    assert report.published_summary is True
    assert (funnel.reviews_attempted, funnel.reviews_clean) == (2, 2)
    assert funnel.directives_issued == 5
    assert funnel.opportunities == 9
    assert (funnel.followed, funnel.revised, funnel.unreferenced) == (4, 4, 1)
    assert funnel.opportunity_rate == 8 / 9
    assert max(funnel.carried_turns) == 11
    assert funnel.eligibility_errors == []
    assert report.ages_unknown == 0
    assert max(report.directive_ages) == 11
    assert cr.carry_contract_errors(report, "claude-code") == []


def test_carry_contract_fails_loud_when_identity_chain_is_missing(tmp_path: Path) -> None:
    report = cr.read(log(tmp_path / "fallback.jsonl", [observation(1, 1, "hurry:none")]))
    errors = cr.carry_contract_errors(report, "claude-code")
    assert "one run identity is required" in errors
    assert "requested brain 'claude-code' not proven (observed: none)" in errors
    assert "positive non-degraded LLM participation is required" in errors
    assert "at least one clean strategic review is required" in errors
    assert "a later decision must explicitly follow an issued directive" in errors


def test_revision_alone_does_not_satisfy_explicit_application(tmp_path: Path) -> None:
    rows = [plan_record(10, issued=["d"], tier="review")]
    rows[0]["brain"] = "claude-code"
    rows.append(plan_record(11, issued=["d"], in_force=["d"], tier="llm"))
    rows[1]["brain"] = "claude-code"
    errors = cr.carry_contract_errors(
        cr.read(log(tmp_path / "revision-only.jsonl", rows)), "claude-code"
    )
    assert "a later decision must explicitly follow an issued directive" in errors


def test_final_turn_issue_is_no_opportunity_not_zero_carry(tmp_path: Path, capsys) -> None:
    report = cr.read(log(tmp_path / "final.jsonl", [plan_record(40, issued=["d"], tier="review")]))
    assert report.funnel.opportunities == 0
    assert report.funnel.opportunity_rate is None
    cr.emit(report)
    assert "UNANSWERABLE — no later eligible decision" in capsys.readouterr().out


def test_later_opportunities_distinguish_follow_override_and_silence(tmp_path: Path) -> None:
    rows = [plan_record(10, issued=["a", "b", "c"], tier="review")]
    rows.append(plan_record(11, in_force=["a", "b", "c"], followed=["a"], overrode=["b"]))
    funnel = cr.read(log(tmp_path / "outcomes.jsonl", rows)).funnel
    assert funnel.opportunities == 3
    assert (funnel.followed, funnel.overrode, funnel.unreferenced) == (1, 1, 1)
    assert funnel.opportunity_rate == 1 / 3
    assert funnel.opportunities == (
        funnel.followed + funnel.revised + funnel.overrode + funnel.unreferenced
    )


def test_duplicate_ids_do_not_multiply_one_directive_decision_pair(tmp_path: Path) -> None:
    rows = [plan_record(10, issued=["d"], tier="review")]
    rows.append(plan_record(11, in_force=["d", "d"], followed=["d", "d"]))
    funnel = cr.read(log(tmp_path / "duplicates.jsonl", rows)).funnel
    assert (funnel.opportunities, funnel.followed) == (1, 1)
    assert funnel.opportunities == (
        funnel.followed + funnel.revised + funnel.overrode + funnel.unreferenced
    )


def test_review_reissue_is_a_revision_with_original_lineage(tmp_path: Path) -> None:
    rows = [plan_record(10, issued=["d"], tier="review")]
    rows.append(plan_record(20, issued=["d"], in_force=["d"], tier="review"))
    funnel = cr.read(log(tmp_path / "revision.jsonl", rows)).funnel
    assert funnel.directives_issued == 1
    assert (funnel.opportunities, funnel.revised) == (1, 1)
    assert funnel.carried_turns == [10]


def test_missing_run_identity_or_applicability_refuses_the_rate(tmp_path: Path) -> None:
    opening = plan_record(10, issued=["d"], tier="review")
    opening.pop("game_id")
    later = plan_record(11)
    later["plan"].pop("in_force")
    funnel = cr.read(log(tmp_path / "missing.jsonl", [opening, later])).funnel
    assert funnel.opportunity_rate is None
    assert (
        "run identity missing (need run_id, game_id, or summary run)" in funnel.eligibility_errors
    )
    assert "applicability missing (plan.in_force absent)" in funnel.eligibility_errors


def test_expiry_is_unanswerable_without_a_retirement_event(tmp_path: Path) -> None:
    rows = [plan_record(10, issued=["d"], tier="review"), plan_record(11, in_force=["d"])]
    funnel = cr.read(log(tmp_path / "expiry.jsonl", rows)).funnel
    assert funnel.expiry_observable is False


def test_review_health_fields_are_required_before_calling_it_clean(tmp_path: Path) -> None:
    row = plan_record(10, issued=["d"], tier="review")
    row.pop("degraded")
    row["plan"].pop("rejected")
    funnel = cr.read(log(tmp_path / "review-health.jsonl", [row])).funnel
    assert (funnel.reviews_attempted, funnel.reviews_clean) == (1, 0)
    assert "review degradation status missing" in funnel.eligibility_errors
    assert "review rejection status missing" in funnel.eligibility_errors


def test_the_m1_baseline_still_reads_zero_carry() -> None:
    """The committed run this design was written against, kept as a regression on the claim.

    If a future change makes this non-zero, either something now carries — which is the goal —
    or the tier mapping drifted. Either way it should not happen silently.
    """
    report = cr.read(REPO / "evals" / "runs" / "na-6db" / "brain.faction7.jsonl")
    assert report.decisions == 1400
    assert report.carried == 0
    assert report.tiers["llm"] == 700
