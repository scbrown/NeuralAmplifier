"""Optionally waking an agent that lives in a tmux pane.

A convenience, and never the mechanism. The guaranteed way an agent finds work is to ask —
``next_decision`` blocks server-side until a decision arrives, so a harness that polls needs no
configuration and cannot be misconfigured. This is the shortcut that saves it from waiting, and
everything downstream must keep working when it silently does nothing.

That ordering is deliberate. A notification channel that other things depend on stops being a
notification channel and becomes a single point of failure with no error reporting — and
``send-keys`` has no error reporting to give: it types, and cannot tell you whether anything
read what it typed.

Nothing here launches anything. The orchestrator does not start tmux, a pane, or an agent; it
rings a pane that already exists, if it was told about one. Setting up the session is the
agent's own job (see ``.claude/skills/play-alpha-centauri``), which is what keeps a harness
swappable — Neural Amplifier never needs to know how to start it.

The event channel, and *only* the event channel. ``tmux send-keys`` types into a pane the way a
person would: there is no return value, no completion signal, and no way to tell a delivered
keystroke from a swallowed one. So it can ring a bell and it cannot carry a decision, and the
split is the whole design — the nudge says *a decision is waiting*, the agent collects the
actual world view over MCP, and its orders come back the same way.

That split is also what keeps the terminal out of the trust boundary. Base names are editable
in-game and faction nouns come from a text file, so every name in a world view is player-supplied.
A base called ``"; rm -rf ~`` is a legal SMAC base name, and putting one on a command line that
something types into a shell-adjacent pane is a vulnerability rather than a formatting problem.
Nothing from the game is ever interpolated into a nudge — the payload is a fixed sentence plus a
decision id this module generated.

Why tmux rather than a socket: every harness worth attaching already runs in a terminal, so a
pane is the one interface Claude Code, a REPL, another agent framework and a human all present.
Wrapping them uniformly means a new harness needs no new integration — just a pane name.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

#: Decision ids are generated in :mod:`.pending` from a surface id and a counter, so they are
#: already tame. Re-checked here anyway: this module's contract is that nothing it sends can be
#: anything other than the literal text below, and a contract enforced at the boundary survives
#: a caller that changes upstream.
_SAFE_ID = re.compile(r"\A[A-Za-z0-9._:-]{1,64}\Z")


class Doorbell:
    """Rings a tmux pane when a decision needs answering.

    Best-effort by construction. A missing tmux, a dead pane or a send-keys failure is logged
    and swallowed: the decision is already on the queue, and an agent that polls
    ``next_decision`` still finds it. Failing the game's turn because a notification did not
    land would make the convenience layer load-bearing.
    """

    def __init__(self, target: str, enabled: bool = True) -> None:
        #: A tmux target — ``session:window.pane``, or just a pane name.
        self.target = target
        self.enabled = enabled and bool(target)
        self._warned = False

    @classmethod
    def from_env(cls) -> Doorbell:
        """Build from ``NA_TMUX_TARGET``. Absent means no doorbell, which is a valid setup:
        an agent can poll ``next_decision`` with a wait instead."""
        return cls(os.environ.get("NA_TMUX_TARGET", ""))

    def ring(self, decision_id: str, surface_id: str | None = None) -> bool:
        """Nudge the pane. Returns whether the keystrokes were delivered.

        The return value is about *delivery*, never about the agent — tmux confirms it typed,
        and nothing more. Treating a ``True`` here as "the agent is working on it" is the one
        misreading this interface invites.
        """
        if not self.enabled:
            return False
        if not _SAFE_ID.match(decision_id):
            log.warning("refusing to ring for unsafe decision id %r", decision_id)
            return False
        if surface_id is not None and not _SAFE_ID.match(surface_id):
            surface_id = None
        if shutil.which("tmux") is None:
            if not self._warned:
                log.warning("NA_TMUX_TARGET is set but tmux is not on PATH; doorbell disabled")
                self._warned = True
            return False

        what = f" ({surface_id})" if surface_id else ""
        message = (
            f"A game decision is waiting{what}: call next_decision to collect it, "
            f"then submit_orders. [{decision_id}]"
        )
        # -l sends the text literally, so tmux never interprets it as key names — without it a
        # payload containing "Enter" or "C-c" would be read as keys rather than characters.
        # Enter is then sent separately and deliberately, which is what submits the prompt.
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", self.target, "-l", message],
                check=True,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", self.target, "Enter"],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            # Once per process: a detached pane would otherwise log on every decision and bury
            # everything else in the service log.
            if not self._warned:
                log.warning("doorbell to %s failed (%s); agent must poll instead", self.target, exc)
                self._warned = True
            return False
        return True
