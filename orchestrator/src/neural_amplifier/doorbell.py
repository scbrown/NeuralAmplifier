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
Nothing from the game is ever interpolated into a nudge — the payload is a fixed sentence, a
decision id generated on this side, and a surface id that had to be in the frozen registry to
appear at all.

**That registry check is the whole of it, and a character whitelist is not a substitute.** A
surface id arrives on the world view, stamped by the adapter, so it is adapter-supplied text on
the same footing as a base name — it merely happens to look tame. Matching it against a pattern
asks whether it could hurt, which is a judgement about shell metacharacters that has to be right
forever; matching it against ``surfaces.ALL`` asks whether it is one of 77 strings this
repository wrote down, which is a fact. An id that is not in the registry is dropped from the
message rather than refused: an unregistered surface is an instrumentation bug, it is already
counted as one on the decision record (``coverage.unknown_surface_ids``), and losing four words
of context in a convenience nudge is not a reason to withhold the nudge.

Why tmux rather than a socket: every harness worth attaching already runs in a terminal, so a
pane is the one interface Claude Code, a REPL, another agent framework and a human all present.
Wrapping them uniformly means a new harness needs no new integration — just a pane name.

**A command template generalises the pane** (seats-and-policy.md §6). ``NA_DOORBELL_CMD`` is a
JSON argv with exactly one ``{message}`` element, run with no shell and a 5 s timeout — for a
crew member, an inbox delivery command that persists the nudge before anything types it. The
rule above does not change with the transport: the message is still one of two fixed sentences
and an id that passed ``_SAFE_ID``, so the template only chooses *where* the bell rings.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess

from .surfaces import ALL

log = logging.getLogger(__name__)

#: Decision ids are generated in :mod:`.pending` from a surface id and a counter, so they are
#: already tame. Re-checked here anyway: this module's contract is that nothing it sends can be
#: anything other than the literal text below, and a contract enforced at the boundary survives
#: a caller that changes upstream.
_SAFE_ID = re.compile(r"\A[A-Za-z0-9._:-]{1,64}\Z")


#: The one placeholder a command template carries. Substituted by plain string replacement, never
#: ``str.format``: any other brace in the template is the operator's literal text.
PLACEHOLDER = "{message}"

#: How long one ring may take before it is abandoned. A ring is a nudge; it never holds anything.
RING_TIMEOUT_S = 5


class DoorbellConfigError(ValueError):
    """The doorbell's environment is contradictory or malformed. Raised at startup, not per ring."""


def parse_command(raw: str) -> list[str]:
    """``NA_DOORBELL_CMD`` as an argv: a JSON list of strings with exactly one ``{message}``.

    Exactly one, counted across the whole argv. Zero means the nudge says nothing, and two means
    the fixed sentence is typed twice into whatever the operator pointed this at; both are
    template mistakes worth refusing at startup rather than discovering in a running game.
    """
    try:
        argv = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DoorbellConfigError(f"NA_DOORBELL_CMD is not JSON: {exc}") from exc
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise DoorbellConfigError("NA_DOORBELL_CMD must be a nonempty JSON list of strings")
    if not argv[0]:
        raise DoorbellConfigError("NA_DOORBELL_CMD: the program (first element) is empty")
    count = sum(a.count(PLACEHOLDER) for a in argv)
    if count != 1:
        raise DoorbellConfigError(
            f"NA_DOORBELL_CMD must contain {PLACEHOLDER} exactly once (found {count})"
        )
    return argv


def decision_sentence(decision_id: str, surface_id: str | None) -> str:
    """The first fixed sentence: a decision is waiting. ``surface_id`` is registry-checked."""
    what = f" ({surface_id})" if surface_id else ""
    return (
        f"A game decision is waiting{what}: call next_decision to collect it, "
        f"then submit_orders. [{decision_id}]"
    )


def review_sentence(review_id: str) -> str:
    """The second fixed sentence: a strategic review is due. Nothing in it comes from the game."""
    return (
        "A strategic review is due for your seat: read the seat's trajectory, then set policy, "
        f"a plan or a directive; standing policy holds until you do. [{review_id}]"
    )


class Doorbell:
    """Rings an agent when a decision needs answering or a review falls due.

    Two transports, one rule. The default types into a tmux pane (``NA_TMUX_TARGET``). A command
    template (``NA_DOORBELL_CMD``) runs an argv instead — ``["st", "inbox", "-d", "AGENT",
    "{message}"]`` delivers to a crew member's inbox, which persists before it types. Either way
    the payload is a fixed sentence plus an id generated on this side, the command runs as an
    argv with no shell, and nothing from the game reaches it.

    Best-effort by construction. A missing tmux, a dead pane, a failing command or a timeout is
    logged once and swallowed: the decision is already on the queue, and an agent that polls
    ``next_decision`` still finds it. Failing the game's turn because a notification did not
    land would make the convenience layer load-bearing.
    """

    def __init__(
        self, target: str = "", enabled: bool = True, command: list[str] | None = None
    ) -> None:
        if target and command:
            raise DoorbellConfigError("set NA_TMUX_TARGET or NA_DOORBELL_CMD, not both")
        #: A tmux target — ``session:window.pane``, or just a pane name.
        self.target = target
        #: A command template, already validated by :func:`parse_command`.
        self.command = parse_command(json.dumps(command)) if command else None
        self.enabled = enabled and bool(target or command)
        self._warned = False

    @classmethod
    def from_env(cls) -> Doorbell:
        """Build from ``NA_TMUX_TARGET`` or ``NA_DOORBELL_CMD``. Neither means no doorbell, which
        is a valid setup: an agent can poll ``next_decision`` with a wait instead. Both is
        refused — which one rings would otherwise be an accident of precedence."""
        target = os.environ.get("NA_TMUX_TARGET", "")
        raw = os.environ.get("NA_DOORBELL_CMD", "")
        if target and raw:
            raise DoorbellConfigError("set NA_TMUX_TARGET or NA_DOORBELL_CMD, not both")
        return cls(target, command=parse_command(raw) if raw else None)

    def ring(self, decision_id: str, surface_id: str | None = None) -> bool:
        """Nudge the agent about a decision. Returns whether the nudge was delivered.

        The return value is about *delivery*, never about the agent — tmux confirms it typed,
        a command confirms it exited zero, and nothing more. Treating a ``True`` here as "the
        agent is working on it" is the one misreading this interface invites.
        """
        if not self.enabled:
            return False
        if not _SAFE_ID.match(decision_id):
            log.warning("refusing to ring for unsafe decision id %r", decision_id)
            return False
        # Registry membership, not a character class. The id is adapter-supplied; being one of
        # the 77 strings this repository froze is what makes it ours to interpolate.
        if surface_id is not None and surface_id not in ALL:
            log.debug("dropping unregistered surface id %r from the nudge", surface_id)
            surface_id = None
        return self._deliver(decision_sentence(decision_id, surface_id))

    def ring_review(self, review_id: str) -> bool:
        """Nudge the strategist that a review is due. Same id rule, second fixed sentence."""
        if not self.enabled:
            return False
        if not _SAFE_ID.match(review_id):
            log.warning("refusing to ring for unsafe review id %r", review_id)
            return False
        return self._deliver(review_sentence(review_id))

    def _deliver(self, message: str) -> bool:
        if self.command is not None:
            return self._run_command(message)
        return self._send_keys(message)

    def _fail_once(self, text: str, *args: object) -> bool:
        # Once per process: a detached pane or a dead inbox would otherwise log on every ring and
        # bury everything else in the service log.
        if not self._warned:
            log.warning(text, *args)
            self._warned = True
        return False

    def _run_command(self, message: str) -> bool:
        assert self.command is not None
        # An argv, never a shell: the message is one element, so a metacharacter in it (there
        # are none in the fixed sentences, and this keeps it that way) is passed literally.
        argv = [a.replace(PLACEHOLDER, message) for a in self.command]
        try:
            subprocess.run(argv, check=True, capture_output=True, timeout=RING_TIMEOUT_S)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            return self._fail_once(
                "doorbell command %s failed (%s); agent must poll instead", argv[0], exc
            )
        return True

    def _send_keys(self, message: str) -> bool:
        if shutil.which("tmux") is None:
            return self._fail_once(
                "NA_TMUX_TARGET is set but tmux is not on PATH; doorbell disabled"
            )
        # -l sends the text literally, so tmux never interprets it as key names — without it a
        # payload containing "Enter" or "C-c" would be read as keys rather than characters.
        # Enter is then sent separately and deliberately, which is what submits the prompt.
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", self.target, "-l", message],
                check=True,
                capture_output=True,
                timeout=RING_TIMEOUT_S,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", self.target, "Enter"],
                check=True,
                capture_output=True,
                timeout=RING_TIMEOUT_S,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            return self._fail_once(
                "doorbell to %s failed (%s); agent must poll instead", self.target, exc
            )
        return True
