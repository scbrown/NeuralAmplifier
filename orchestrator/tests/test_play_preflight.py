"""The pre-game checklist — `scripts/play-preflight.py`, `just play check`.

Two of these checks exist because the failure they catch does not look like a failure.

A running orchestrator with the *wrong brain* is healthy in every observable way: decisions are
answered, the log fills, the game plays on. The only symptom is an agent polling a queue whose
endpoints were never mounted.

`llm_timeout_ms` at its 2500 ms default is worse, because before the deadline coupling landed it
looked like *success* — a late answer was accepted and recorded `tier: llm, degraded: false` for
a turn the engine had already decided (na-t3h).

So the tests worth having are about **classification**: does a silent wrong answer come out
blocking, and does an absent optional component stay optional. A preflight that fails on a
missing nicety is one people learn to skip, and then it is not catching the two above either.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "play-preflight.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("play_preflight", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pre = _load()


def rows(report: Any) -> dict[str, tuple[str, str]]:
    """{what: (status, blocking|optional)} — the classification, which is the whole point."""
    return {what: (status, kind) for status, kind, what, _ in report.rows}


# --- the brain, which is the check that matters most ------------------------


def test_the_wrong_brain_is_blocking_not_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure this script exists for. A `claude` or `scripted` brain answers every
    decision itself, so the agent endpoints are never mounted and an attached agent waits
    forever next to a game that is playing perfectly well without it."""
    monkeypatch.setattr(pre, "get_json", lambda url, timeout=2.0: {"brain": "claude", "ok": True})

    report = pre.Report()
    pre.check_orchestrator(report, "http://x")

    assert rows(report)["orchestrator"] == (pre.FAIL, "blocking")
    assert report.blocked is True


def test_an_unreachable_orchestrator_is_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pre, "get_json", lambda url, timeout=2.0: None)

    report = pre.Report()
    pre.check_orchestrator(report, "http://x")

    assert report.blocked is True


def test_the_agent_brain_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pre, "get_json", lambda url, timeout=2.0: {"brain": "agent", "game_id": "game-1"}
    )

    report = pre.Report()
    pre.check_orchestrator(report, "http://x")

    assert rows(report)["orchestrator"][0] == pre.OK
    assert report.blocked is False


def test_a_failing_telemetry_sink_does_not_block_the_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """The evidence is partial and the game is still playable. Blocking here would be the
    preflight deciding something it does not get to decide."""
    monkeypatch.setattr(
        pre,
        "get_json",
        lambda url, timeout=2.0: {
            "brain": "agent",
            "game_id": "g",
            "telemetry": {"healthy": False, "failures": ["DecisionLog"]},
        },
    )

    report = pre.Report()
    pre.check_orchestrator(report, "http://x")

    assert rows(report)["telemetry"] == (pre.WARN, "optional")
    assert report.blocked is False


# --- llm_timeout_ms ---------------------------------------------------------


def write_ini(tmp_path: Path, body: str) -> Path:
    (tmp_path / "thinker.ini").write_text(body, encoding="utf-8")
    return tmp_path


def test_the_default_timeout_is_blocking(tmp_path: Path) -> None:
    """2500 ms is the built-in default and no agent meets it. Every decision handed over has
    already been answered by the deterministic tier."""
    report = pre.Report()
    pre.check_timeout(report, str(write_ini(tmp_path, "llm_timeout_ms=2500\n")))

    assert rows(report)["llm_timeout_ms"] == (pre.FAIL, "blocking")


def test_an_absent_setting_is_blocking_because_the_default_is_the_problem(tmp_path: Path) -> None:
    """Absent is not neutral here — it *is* 2500 ms, which is exactly the case above."""
    report = pre.Report()
    pre.check_timeout(report, str(write_ini(tmp_path, "difficulty=4\n")))

    assert rows(report)["llm_timeout_ms"] == (pre.FAIL, "blocking")


def test_zero_means_the_engine_waits(tmp_path: Path) -> None:
    report = pre.Report()
    pre.check_timeout(report, str(write_ini(tmp_path, "llm_timeout_ms=0\n")))

    assert rows(report)["llm_timeout_ms"][0] == pre.OK


def test_a_generous_finite_timeout_passes(tmp_path: Path) -> None:
    report = pre.Report()
    pre.check_timeout(report, str(write_ini(tmp_path, "llm_timeout_ms=120000\n")))

    assert rows(report)["llm_timeout_ms"][0] == pre.OK


def test_the_last_setting_wins(tmp_path: Path) -> None:
    """thinker.ini is edited by hand and by scripts, and a duplicated key is common. The
    engine reads the last one, so a checker that reads the first would clear a run that is
    about to be answered at 2500 ms."""
    report = pre.Report()
    pre.check_timeout(report, str(write_ini(tmp_path, "llm_timeout_ms=0\nllm_timeout_ms=2500\n")))

    assert rows(report)["llm_timeout_ms"] == (pre.FAIL, "blocking")


def test_no_play_dir_warns_rather_than_blocking(tmp_path: Path) -> None:
    """The orchestrator half of a run is testable with no game on the machine at all, which is
    how most of this repo's work happens. Blocking would make the check unusable there."""
    report = pre.Report()
    pre.check_timeout(report, None)

    assert rows(report)["llm_timeout_ms"] == (pre.WARN, "optional")
    assert report.blocked is False


# --- the optional half ------------------------------------------------------


def test_grounding_and_the_guard_never_block(tmp_path: Path) -> None:
    """Both improve a game and neither is required to play one. A checklist that fails on
    them is a checklist people learn to skip — and then it is not catching the brain either."""
    report = pre.Report()
    pre.check_quipu(report, None)
    pre.check_yupana(report, None)

    assert rows(report)["quipu (grounding)"] == (pre.WARN, "optional")
    assert rows(report)["yupana (guard)"] == (pre.WARN, "optional")
    assert report.blocked is False


def test_the_repo_ships_an_mcp_config() -> None:
    """The step this removes: without `.mcp.json`, attaching means remembering a `claude mcp
    add` line with a --directory flag in it."""
    report = pre.Report()
    pre.check_mcp_config(report, REPO)

    assert rows(report)["mcp config"][0] == pre.OK
