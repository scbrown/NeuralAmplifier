"""The unattended driver's PROGRESS cap — na-cp5.

A wall-clock cap answers the wrong question. It stops a run for being LONG, and length is not
the failure mode: ladder-attempt4 plays 250 turns at a measured 2.4 min/turn, needs ~10h, and is
healthy the whole way. Its 21600s cap killed it at 04:36 while it was advancing normally, ~18
minutes short of its own target, and the wall clock could not tell that from a run that died at
turn 3.

The replacement is a NO-TURN-ADVANCE window. What these arms are really defending is the number
and the placement, because the obvious version of this fix is worse than the bug:

* **The number.** On the same row, turn 78 spent 107.5 minutes without advancing — a provider
  outage — and then recovered and played on to turn 155+ clean, logging 43 BUSY classifications
  and zero STALLED across the window. Any window at or below ~108 minutes would have destroyed a
  row that fully recovered. `test_a_stall_shorter_than_the_limit_is_survivable` is that row.

* **The placement.** The check sits BEFORE the poll. The no-result branch `continue`s, so a
  check written after it is skipped for exactly the silences that make a run stop progressing —
  it would fire only on runs still answering, which are the runs least likely to need it.
  `test_an_outage_counts_against_progress` fails on that version.

Every arm drives the REAL main loop on a fake clock, because the thing under test is measured in
hours. Only the poll responses and the clock differ between them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time as real_time
import types
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "scripts" / "drive-unattended.py"

#: Seconds the fake clock advances per driver cycle. 300s x 90 BUSY cycles is 7.5h, comfortably
#: past the 3h cap, so the outage arm proves the cap fires BEFORE BUSY_LIMIT rather than racing
#: it — an arm that let BUSY_LIMIT win would pass while testing nothing about progress.
TICK = 300.0

#: Hard stop for an arm whose cap never fires. ~14x the cycles the 3h limit needs at TICK.
CALL_CEILING = 500


class _Clock:
    """A clock the arms drive by hand. `strftime` stays real: the log only needs to be legible."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, _s: float) -> None:
        self.now += TICK

    def strftime(self, fmt: str) -> str:
        return real_time.strftime(fmt)


def _load(clock: _Clock) -> Any:
    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        pil.Image = object  # type: ignore[attr-defined]
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = object  # type: ignore[assignment]
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("drive_unattended_progress_under_test", DRIVER)
    assert spec and spec.loader
    mod: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time = clock
    # Bar detection is not under test and needs Pillow; every arm keeps the turn below the
    # viability checkpoint so no `game-state` probe runs either. The size must be NON-ZERO:
    # a real unreadable shot raises and is handled, but a readable 0x0 one is a state the game
    # never produces, and faking it walks the driver into a branch that sets no log note.
    mod.bars = lambda _p: ([], (1024, 768))
    # No arm may reach the real input layer. These drive the actual X server.
    mod.key = lambda *_a, **_k: None
    mod.click = lambda *_a, **_k: None
    return mod


def _run(tmp_path: Path, responder: Callable[[int], Any], wall: str = "0") -> tuple[str, Any]:
    """Drive main() with `responder(call_index)` answering every poll. Returns (log, module)."""
    game = tmp_path / "play"
    game.mkdir(parents=True)
    log = tmp_path / "turns.log"
    clock = _Clock()
    argv = sys.argv
    sys.argv = ["drive-unattended.py", str(game), str(log), wall]
    try:
        mod = _load(clock)
        calls = {"n": 0}
        ticks = {"n": 1000}

        def cmd(_line: str, wait: float = 15.0) -> Any:
            calls["n"] += 1
            # A run with no working cap has no other stopping condition, so without this the
            # arm hangs instead of failing. Measured: sabotaging the check away turned a
            # 0.02s suite into a wedged process, which reads as an infrastructure problem
            # rather than as the regression it is.
            if calls["n"] > CALL_CEILING:
                raise AssertionError(
                    "driver ran %d cycles without stopping — no cap fired" % CALL_CEILING
                )
            clock.now += 1.0
            out = responder(calls["n"])
            if out is _BUSY:
                # A live, ADVANCING heartbeat behind an unanswered poll: the game is working.
                ticks["n"] += 5
                (game / "na-input-heartbeat").write_text(
                    json.dumps({"ticks": ticks["n"], "hwnd": 1, "halted": 0})
                )
                return None
            return out

        mod.cmd = cmd
        mod.main()
    finally:
        sys.argv = argv
    return log.read_text(), mod


class _Busy:
    """Sentinel: this poll goes unanswered, but the game's heartbeat advances behind it."""


_BUSY = _Busy()
STOPPED = "stopping for lack of PROGRESS"


def test_a_run_that_stops_advancing_is_stopped(tmp_path: Path) -> None:
    """The turn sits still while hours pass. That is the failure the cap exists for."""
    out, _ = _run(tmp_path, lambda _n: {"turn": 5, "halted": 0})
    assert STOPPED in out
    assert "best turn 5" in out
    assert "not for elapsed time" in out, "the log must say WHY it stopped, or it reads as a timeout"


def test_a_run_that_keeps_advancing_is_not_stopped(tmp_path: Path) -> None:
    """The control. Without it every arm above would pass on a cap that fires unconditionally."""
    def responder(n: int) -> Any:
        if n > 60:
            return None  # no heartbeat file -> terminal at SILENT_LIMIT, an unrelated exit
        return {"turn": n, "halted": 0}

    out, _ = _run(tmp_path, responder)
    assert STOPPED not in out
    assert "the game is gone" in out, "arm did not reach its intended exit; it proved nothing"


def test_a_stall_shorter_than_the_limit_is_survivable(tmp_path: Path) -> None:
    """THE REGRESSION — ladder-attempt4 turn 78: 107.5 min of no advance, then full recovery.

    A cap tight enough to feel responsive kills this row. The measured worst recoverable stall
    is the floor under the constant, and this arm is what holds it there.
    """
    stall_cycles = int((107.5 * 60) / TICK) + 1

    def responder(n: int) -> Any:
        if n > 60:
            return None
        # Recovers and KEEPS ADVANCING, as turn 78 did. A responder that stalls and then
        # freezes one turn later is just a slower version of the stalled arm, and it trips the
        # cap for the right reason — which would look like this arm failing.
        return {"turn": 5 if n <= stall_cycles else 5 + (n - stall_cycles), "halted": 0}

    out, mod = _run(tmp_path, responder)
    assert mod.NO_PROGRESS_LIMIT > 107.5 * 60, (
        "the limit is at or below the measured worst RECOVERABLE stall, so it would have killed "
        "a row that recovered — the same mistake as the wall clock, better dressed"
    )
    assert STOPPED not in out
    assert "the game is gone" in out


def test_an_outage_counts_against_progress(tmp_path: Path) -> None:
    """A silence with a live heartbeat still makes no progress, and must still be capped.

    This is the placement arm. The no-result branch `continue`s, so a check written after it
    never runs here and the driver rides the outage until BUSY_LIMIT — a different limit, a
    different message, and 7.5h of wall clock at this cadence.
    """
    out, mod = _run(tmp_path, lambda _n: _BUSY)
    assert STOPPED in out, "the progress cap never fired on an unanswered poll"
    assert "BUSY: heartbeat advancing" in out, "arm did not exercise the busy path at all"
    assert "alive and never answering" not in out, "BUSY_LIMIT won the race; the cap was too late"
    assert out.count("BUSY: heartbeat advancing") < mod.BUSY_LIMIT


def test_a_turn_going_backwards_is_not_progress(tmp_path: Path) -> None:
    """A reload rewinds the turn. Counting that as progress hands a reload loop an endless budget."""
    out, _ = _run(tmp_path, lambda n: {"turn": -n, "halted": 0})
    assert STOPPED in out
    # STRICTLY decreasing and never repeating. The first version of this arm used
    # `max(1, 40 - n)`, which saturates at 1 — after 39 cycles the turn stopped CHANGING, so
    # a driver that (wrongly) counted any change as progress still tripped the cap and the arm
    # still passed. It was measuring saturation, not the rule. Found by sabotage.


def test_zero_wall_clock_means_no_wall_clock_cap(tmp_path: Path) -> None:
    """`0` is how a row asks to be bounded by PROGRESS alone — not how it asks to be unbounded."""
    _, mod = _run(tmp_path, lambda _n: {"turn": 5, "halted": 0}, wall="0")
    assert mod.DEADLINE is None
    assert mod.NO_PROGRESS_LIMIT > 0, "a run with neither cap would never stop"
