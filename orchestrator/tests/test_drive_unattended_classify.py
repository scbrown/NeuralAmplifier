"""The unattended driver's no-result classification — na-xl3.

An unanswered `shot` poll has THREE causes and the driver used to charge all three to one
counter, then stop at six and write "the game is gone, or a modal has deafened the channel".

The third cause is the one that was missing: the input result channel is unavailable while a
model decision is synchronously in flight, so a HEALTHY, BUSY game goes silent for as long as
the decision takes. MEASURED on ladder-attempt4 turn 57 — four back-to-back `base.production`
decisions of 29.5s / 26.7s / 47.7s / 15.8s produced a FIVE-consecutive silence, one short of
the limit, while the game's own heartbeat advanced at its normal ~5 ticks/second throughout.
Decisions per turn rose 3.40 -> 9.78 over turns 0-48 and the plan in force mandates 20 bases by
turn 80, so that burst only gets longer.

The game's own heartbeat is the discriminator, and these arms are the reason to trust it:
ADVANCING is the game working; FROZEN is a stall or a deafening modal; ABSENT is a dead game.
Only the last two are terminal at SILENT_LIMIT.

Every arm drives the REAL main loop with a channel that never answers. What differs between
them is only the heartbeat, which is the whole claim.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "scripts" / "drive-unattended.py"


class _UnusedImage:
    """Stands in for `PIL.Image`, which these arms must never reach.

    `_dialog_bars` imports Pillow at module scope and Pillow is not an orchestrator test
    dependency, so importing the driver would fail here for a reason that has nothing to do with
    what is under test. Bar detection only runs AFTER a poll is answered, and in every arm below
    no poll is ever answered — so a stub is honest rather than a shortcut. It raises if touched,
    which is what keeps that claim from quietly becoming false.
    """

    @staticmethod
    def open(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not be reached
        raise AssertionError("bar detection ran: an arm answered a poll it was meant to drop")


def _run(tmp_path: Path, arm: str) -> str:
    """Run main() to completion against a channel that never answers. Returns the turn log."""
    game = tmp_path / "play"
    game.mkdir(parents=True)
    log = tmp_path / "turns.log"
    if arm != "absent":
        (game / "na-input-heartbeat").write_text(
            json.dumps({"ticks": 1000, "hwnd": 1, "halted": 0})
        )

    argv = sys.argv
    sys.argv = ["drive-unattended.py", str(game), str(log), "600"]
    try:
        if "PIL" not in sys.modules:
            pil = types.ModuleType("PIL")
            pil.Image = _UnusedImage  # type: ignore[attr-defined]
            sys.modules["PIL"] = pil
            sys.modules["PIL.Image"] = _UnusedImage  # type: ignore[assignment]
        sys.path.insert(0, str(REPO / "scripts"))
        spec = importlib.util.spec_from_file_location("drive_unattended_under_test", DRIVER)
        assert spec and spec.loader
        mod: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        ticks = {"n": 1000}

        def never_answers(_line: str, wait: float = 15.0) -> None:
            if arm == "advancing":
                ticks["n"] += 5
                (game / "na-input-heartbeat").write_text(
                    json.dumps({"ticks": ticks["n"], "hwnd": 1, "halted": 0})
                )
            return None

        mod.cmd = never_answers
        mod.time.sleep = lambda _s: None
        mod.main()
    finally:
        sys.argv = argv
    return log.read_text()


def test_busy_game_is_not_declared_dead(tmp_path: Path) -> None:
    """A live, advancing heartbeat must NOT be spent against SILENT_LIMIT.

    This is the regression. On the old rule this arm stopped after six cycles and logged that
    the game was gone, of a game that was demonstrably alive.
    """
    out = _run(tmp_path, "advancing")
    assert "BUSY: heartbeat advancing" in out
    assert out.count("BUSY: heartbeat advancing") > 6, "stopped inside the old six-cycle limit"
    assert "the game is gone" not in out


def test_busy_is_still_bounded(tmp_path: Path) -> None:
    """A game that ticks forever and never answers is also a failure — with its OWN words."""
    out = _run(tmp_path, "advancing")
    assert "alive and never answering" in out
    assert out.count("BUSY: heartbeat advancing") == 90


def test_frozen_heartbeat_is_terminal(tmp_path: Path) -> None:
    """A present but FROZEN heartbeat is a stall or a deafening modal: stop at SILENT_LIMIT."""
    out = _run(tmp_path, "frozen")
    assert "heartbeat FROZEN at 1000" in out
    assert "no advancing heartbeat" in out
    assert "BUSY" not in out
    assert out.count("no-result") == 6


def test_absent_heartbeat_is_terminal(tmp_path: Path) -> None:
    """No heartbeat file at all is a dead game: stop at SILENT_LIMIT."""
    out = _run(tmp_path, "absent")
    assert "no heartbeat file" in out
    assert "no advancing heartbeat" in out
    assert "BUSY" not in out
    assert out.count("no-result") == 6


def test_every_no_result_line_carries_its_evidence(tmp_path: Path) -> None:
    """The classification must be re-checkable from the log alone, not taken on trust."""
    for arm in ("advancing", "frozen", "absent"):
        for line in _run(tmp_path / arm, arm).splitlines():
            if "no-result" in line:
                assert "ticks=" in line and "hb_age=" in line, line
