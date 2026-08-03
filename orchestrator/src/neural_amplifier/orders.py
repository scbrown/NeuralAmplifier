"""Issuing orders the engine did not ask us for — the agent's own initiative.

Every other path in this service is REACTIVE: the adapter posts a world view, we answer, the
engine applies it. That can only ever address the one thing the engine chose to ask about, in
the order it walks its bases. A human player is not so constrained — they select the unit they
care about and order it, without waiting for the activation cycle — and that is what makes a
*dependent* move possible, because a move whose sense depends on another unit having already
moved can simply be ordered second.

This is the client half of the adapter's command channel (`na_order_command` in
thinker/src/neural.cpp). See `docs/turn-scoped-play.md`.

**It is a file channel, so the orchestrator must share a filesystem with the game.** The adapter
polls `na-command` at 4 Hz from its window procedure and writes `na-command-result`. Nothing else
in this service requires co-location; this does, and a remote orchestrator will order nothing
while looking healthy.

Two properties do the real work here.

**One order at a time.** The adapter reads a single line and its result file is a single slot
that the next order overwrites. Fire two quickly and the first result is lost — so a lock
serialises them, and each order runs "clear the slot, write the command, wait for the slot to
refill". Without that an order's *reported* outcome could belong to a different order.

**An unobserved result is never success.** If the result does not appear before the deadline the
answer is ``unknown``, not ``ok``. The adapter consumes the command file *before* acting, so a
command that vanished may have run, may have crashed the game, or may have been eaten by a game
that was not pumping messages — and every one of those looks identical from here. Reporting an
unobserved order as applied is the exact failure this project keeps meeting: a knob that reports
success and does nothing.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Command and result filenames, fixed by the adapter (`NA_CMD_PATH`, `NA_CMD_RESULT`).
COMMAND_FILE = "na-command"
RESULT_FILE = "na-command-result"

#: The adapter polls at 4 Hz, so a round trip is ~250 ms plus the work. Five seconds is twenty
#: polls: long enough that an ordinary order is never reported unknown, short enough that a game
#: sitting in a modal does not hang an agent.
DEFAULT_TIMEOUT_S = 5.0

#: How often we look for the result. Well under the adapter's own poll interval so the wait is
#: bounded by the game, not by us.
POLL_S = 0.05

VERBS = ("move", "skip", "build")


@dataclass(frozen=True)
class OrderResult:
    """What happened, and — when we do not know — that we do not know."""

    #: ``ok`` the adapter ran it and said so. ``refused`` it ran and declined, with a reason.
    #: ``unknown`` no result appeared: it may or may not have happened.
    #: ``unavailable`` no game directory is configured, so nothing was written at all.
    status: Literal["ok", "refused", "unknown", "unavailable"]
    command: str
    detail: str = ""
    turn: int | None = None
    halted: int | None = None
    waited_s: float = 0.0

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "status": self.status,
            "command": self.command,
            "detail": self.detail,
            "waited_s": round(self.waited_s, 3),
        }
        if self.turn is not None:
            out["turn"] = self.turn
        if self.halted is not None:
            out["halted"] = self.halted
        return out


class OrderChannel:
    """Writes orders to the game and waits for its answer."""

    def __init__(self, game_dir: str | Path | None, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._dir = Path(game_dir) if game_dir else None
        self._timeout_s = timeout_s
        #: Serialises orders. The channel is one file and one result slot; concurrency here does
        #: not go slowly wrong, it goes silently wrong.
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._dir is not None and self._dir.is_dir()

    def why_unavailable(self) -> str:
        if self._dir is None:
            return "no game directory configured (set NA_GAME_DIR or run.game_dir)"
        if not self._dir.is_dir():
            return f"game directory {self._dir} does not exist"
        return ""

    def issue(self, command: str, timeout_s: float | None = None) -> OrderResult:
        """Send one order and wait for the adapter's result.

        `command` is the raw channel line, e.g. ``move 12 40 21``. Validation of the verb's
        arguments belongs to the adapter, which can ask the engine; duplicating it here would be
        a second opinion about the rules, and the engine is the only authority worth having.
        """
        command = command.strip()
        if not command:
            return OrderResult(status="refused", command="", detail="empty order")
        if not self.available:
            return OrderResult(status="unavailable", command=command, detail=self.why_unavailable())

        assert self._dir is not None  # narrowed by .available
        deadline_s = self._timeout_s if timeout_s is None else timeout_s
        cmd_path = self._dir / COMMAND_FILE
        res_path = self._dir / RESULT_FILE

        with self._lock:
            started = time.monotonic()
            # Clear the slot FIRST. The result file persists from the previous order, so without
            # this the very first read would return a stale success and attribute it to this
            # order — which is worse than no answer, because it is a confident wrong one.
            try:
                res_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                return OrderResult(
                    status="unknown",
                    command=command,
                    detail=f"could not clear {RESULT_FILE}: {exc}",
                )

            try:
                # The adapter reads one line with fgets and removes the file before acting.
                cmd_path.write_text(command + "\n", encoding="utf-8")
            except OSError as exc:
                return OrderResult(
                    status="unknown", command=command, detail=f"could not write order: {exc}"
                )

            while time.monotonic() - started < deadline_s:
                payload = _read_result(res_path)
                if payload is not None:
                    waited = time.monotonic() - started
                    ok = bool(payload.get("ok"))
                    return OrderResult(
                        status="ok" if ok else "refused",
                        command=str(payload.get("command") or command),
                        detail=str(payload.get("detail") or ""),
                        turn=_as_int(payload.get("turn")),
                        halted=_as_int(payload.get("halted")),
                        waited_s=waited,
                    )
                time.sleep(POLL_S)

            waited = time.monotonic() - started
            consumed = not cmd_path.exists()
            # Both branches are `unknown`, deliberately. "The game took the file" is evidence it
            # read the order, not evidence it carried it out — the adapter removes the command
            # BEFORE acting, precisely so a crash cannot replay it. Calling that success would
            # turn a crash into a reported order.
            detail = (
                "the game consumed the order but reported no result before the deadline"
                if consumed
                else "the game never read the order (is it running, and pumping messages?)"
            )
            return OrderResult(status="unknown", command=command, detail=detail, waited_s=waited)


def _read_result(path: Path) -> dict[str, object] | None:
    """The result, or None if it is not there yet.

    A partially written file parses as nothing and is treated as "not yet": the adapter writes it
    in one `fopen`/`fputs`/`fclose`, so a half-file is a race we lose by waiting one more poll,
    not an error worth reporting.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def build_command(verb: str, args: list[int]) -> str:
    """Assemble a channel line, refusing a shape the adapter would only reject.

    This is argument *arity*, not game legality — whether a unit may reach a tile is the engine's
    question and is asked there. The distinction matters: a check here that duplicated an engine
    rule would drift from it silently.
    """
    verb = verb.strip().lower()
    if verb not in VERBS:
        raise ValueError(f"unknown order verb {verb!r}; expected one of {', '.join(VERBS)}")
    expected = {"move": 3, "skip": 1, "build": 2}[verb]
    if len(args) != expected:
        raise ValueError(
            f"{verb} takes {expected} argument(s), got {len(args)} — "
            f"move <veh_id> <x> <y>, skip <veh_id>, build <base_id> <item_id>"
        )
    return " ".join([verb, *(str(int(a)) for a in args)])
