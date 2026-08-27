"""A brain that reaches the model through the Claude Code CLI.

The measurement lane for the way the game is now actually played.

Since the agent pivot the brain is *whatever MCP client attaches* — in practice Claude Code.
But :mod:`.agent_brain` is an interactive session: one context that persists across a whole
game, which is exactly what a stability sample must not be. Run the same world view through it
ten times and you measure a conversation, not a decision.

``claude -p`` gives the same model with none of that. Each call is a fresh process with a fresh
context, so N runs are N independent samples — the property the whole measurement discipline
rests on. It also needs no API key: it uses whatever credentials Claude Code already has, which
is why the "this needs a paid API call" framing that :class:`~.brain.ClaudeBrain` implied is no
longer the only option.

**Comparability, honestly stated.** This shares ``brain._SYSTEM`` verbatim with ``ClaudeBrain``
so a stability figure from one is meaningful beside the other. One difference cannot be papered
over: ``ClaudeBrain`` uses the API's structured output, which makes a malformed reply
impossible, while this asks for JSON and parses it. A parse failure here is a degraded decision
rather than a retry, and it is counted (:attr:`ClaudeCodeBrain.malformed`) rather than hidden —
a measurement lane that silently dropped its own failures would be worse than no lane.

Never reached by the default test suite or by ``build_brain``: it costs money per call, so it is
opt-in at the harness (``--brain claude-code``) exactly as ``NA_BRAIN=claude`` gates the other.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .brain import _SYSTEM, DEFAULT_MODEL, BrainError
from .contract import Orders, WorldView

#: Long, because the point is a considered decision rather than a fast one, and a fresh CLI
#: process pays start-up on every call. Short enough that a wedged run fails the harness instead
#: of holding it forever.
TIMEOUT_SECONDS = 300

#: Appended to the shared system prompt. `claude -p` has no structured-output mode, so the
#: schema has to be stated in words — and stated *last*, because it is the instruction most
#: likely to be diluted by everything above it.
#:
#: EVERY field the decision loop reads must appear here, including the ones that are usually
#: empty. Omitting `directives` from this block made the model issue none in ten runs while the
#: system prompt explained at length that it could — the schema silently overrode the prose, and
#: an ablation of that prose measured nothing but its length. `Orders.cited` records the same
#: failure from the API side: a field the model does not see in the shape it is asked for is a
#: field it does not fill, whatever the instructions say.
_JSON_INSTRUCTION = """

## Output

Return ONLY a JSON object and nothing else — no prose before or after, no markdown fence.

{
  "choices": [{"action_id": "<an id from action_space>", "reason": "<why>"}],
  "cited": ["<grounding fact ids that actually influenced this>"],
  "followed": ["<directive ids you obeyed>"],
  "overrode": ["<directive ids you deliberately went against>"],
  "directives": [
    {
      "id": "<short-kebab-id>",
      "intent": "<what this plan is for, in a sentence>",
      "metric": "<a name from the metric vocabulary above>",
      "comparator": "at_least | at_most | increase | decrease | hold",
      "target": <number, required for at_least/at_most, omit for the others>,
      "priority": <1-10>,
      "entities": ["<datalinks ids this plan is about>"]
    }
  ]
}

`choices` must contain exactly one entry unless the world view's action space is genuinely
asking for several. Every `action_id` must appear verbatim in `action_space`.

`directives` is usually `[]`. Issue one only on a decision whose reasoning should outlive the
turn — see the section above for when that applies and what makes a plan checkable.
"""


#: Substrings that identify a failure the upstream service itself calls TEMPORARY.
#:
#: MEASURED on ladder-attempt4, 2026-08-24 — and only visible because the reason stopped being
#: blank. Every one of the row's fallbacks was:
#:
#:     API Error: 529 Overloaded. This is a server-side issue, usually temporary
#:
#: which had been recorded as `claude -p exited 1: ` with the cause as the empty string. I read
#: the blank as local contention and reported it as such; the account had 17% of its five-hour
#: and 46% of its seven-day budget left, which never fitted that story. It was upstream.
#:
#: A usage or quota limit is deliberately NOT here. Retrying one of those cannot succeed for
#: hours, so a retry would spend the game thread's time on a certain failure — the opposite of
#: what this list is for.
TRANSIENT_MARKERS = (
    "529",
    "overloaded",
    "api error: 500",
    "api error: 502",
    "api error: 503",
    "internal server error",
    "connection error",
    "connection reset",
)

#: Retry a transient failure this many times before giving up. ONE by default, and the reason is
#: cost, not caution: a failing attempt on this row took 185,994 ms, because the CLI already
#: retries internally before it reports. So each extra attempt costs another ~3 minutes of a
#: BLOCKED GAME THREAD, and a generous retry count would turn one decision into a ten-minute
#: stall. One retry converts a 3-minute failure into either a real decision or a 6-minute one,
#: which is the trade worth making once and not three times.
#: NAMED IN ATTEMPTS, not in retries, because "retries=2" is ambiguous in the way that costs
#: money here: it can mean two retries (three attempts) or two attempts (one retry), and at a
#: measured ~186s per failing attempt that is 558s versus 372s of BLOCKED GAME THREAD for one
#: decision. A cross-arm ruling that both arms use the same value is only meaningful if both
#: read the same number the same way, so the unit is in the name and the manifest records it.
#:
#: 2 attempts == 1 retry == the behaviour shipped in c3ff10b.
TRANSIENT_ATTEMPTS_DEFAULT = 2


def transient_attempts() -> int:
    """Total attempts for a transient upstream failure, from `NA_TRANSIENT_ATTEMPTS`.

    Configurable rather than constant so a run's manifest can RECORD what was in force and a
    reader can check the claim against the environment. A hardcoded constant makes a manifest
    line unverifiable: it records a number nobody can confirm was actually used.

    Clamped at 1 below and 4 above. Below 1 is not a run; above 4 spends more than ten minutes
    of blocked game thread on a single decision, which is the regime where retrying makes
    throughput WORSE rather than better (see na-cp5).
    """
    raw = os.environ.get("NA_TRANSIENT_ATTEMPTS")
    if raw is None:
        return TRANSIENT_ATTEMPTS_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return TRANSIENT_ATTEMPTS_DEFAULT
    return max(1, min(4, value))


#: Wait between attempts. Short, because 529 clears in seconds when it clears at all, and the
#: expensive part is the attempt rather than the gap.
RETRY_BACKOFF_SECONDS = 2.0

PROVIDER_EVENT_LOG = Path(
    os.environ.get(
        "MODEL_PROVIDER_EVENT_LOG",
        "~/.local/state/model-provider-calls.jsonl",
    )
).expanduser()


def _record_provider_call(outcome: str, reason: str = "none") -> None:
    """Append one provider-call outcome for the host telemetry collector.

    This intentionally has no network dependency: losing monitoring must never
    make a model call fail.  The JSONL seam is provider-neutral so other CLI
    callers can emit the same three fields without importing this package.
    """
    event = {
        "timestamp": time.time(),
        "provider": "anthropic",
        "outcome": outcome,
        "reason": reason,
    }
    try:
        PROVIDER_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PROVIDER_EVENT_LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _failure_class(reason: str) -> str:
    low = reason.lower()
    if any(marker in low for marker in ("spend limit", "usage limit", "quota", "billing")):
        return "quota"
    if _is_transient(reason):
        return "transient"
    return "other"


def _is_transient(reason: str) -> bool:
    """Whether a failure reason is one the service itself describes as temporary."""
    low = reason.lower()
    if "usage limit" in low or "quota" in low:
        return False
    return any(marker in low for marker in TRANSIENT_MARKERS)


def _why_it_failed(done: subprocess.CompletedProcess[str]) -> str:
    """The diagnosis is on STDOUT, and reporting only stderr throws it away.

    MEASURED 2026-08-24 against the real CLI (`claude -p --output-format json` with a bad
    model): stdout carried 1265 bytes of parseable JSON including
    `result: "There's an issue with the selected model ... it may not exist or you may not
    have access to it"`, while stderr carried 98 bytes of machine tag. This code reported
    `done.stderr[:300]` and discarded stdout entirely.

    That is not merely lossy. On ladder-attempt4 the failures arrived with an EMPTY stderr, so
    19 fallback decisions were recorded with `degrade_reason="claude -p exited 1: "` — the cause
    named as the empty string — while the explanation sat unread in stdout. A degradation whose
    reason is blank cannot be told apart from a rate limit, a usage cap, a timeout or a crash,
    which are four different remedies.

    Order matters: `result` first because it is the sentence a human needs, then raw stdout in
    case the envelope shape changes, then stderr, and finally an explicit statement that BOTH
    streams were empty — because "no output" is itself a finding and must not render as a
    trailing colon.
    """
    out = (done.stdout or "").strip()
    err = (done.stderr or "").strip()
    parts: list[str] = []
    if out:
        try:
            envelope = json.loads(out)
        except ValueError:
            parts.append(f"stdout: {out[:300]}")
        else:
            result = str(envelope.get("result") or "").strip()
            parts.append(f"result: {result[:300]}" if result else f"stdout: {out[:300]}")
    if err:
        parts.append(f"stderr: {err[:200]}")
    return " | ".join(parts) if parts else "both stdout and stderr were EMPTY"


class ClaudeCodeBrain:
    """Runs one decision through ``claude -p``, in a fresh process each time."""

    name = "claude-code"

    def __init__(
        self,
        model: str | None = None,
        timeout: int = TIMEOUT_SECONDS,
        issue_directives: bool = True,
    ) -> None:
        #: None lets Claude Code use its configured default rather than pinning one here. A
        #: pinned default would silently diverge from whatever the interactive agent is running,
        #: and the point of this lane is to measure *that*.
        self.model = model
        self.timeout = timeout
        #: Whether the prompt invites the decision to also set direction. The switch exists for
        #: one specific measurement (na-43h): does a model asked to both decide AND plan do
        #: either worse? That is unanswerable without being able to turn it off.
        self.issue_directives = issue_directives
        self.calls: list[WorldView] = []
        #: Replies that did not parse. Surfaced rather than swallowed — a lane that hid its own
        #: failure rate would report stability it had not measured.
        self.malformed = 0
        self.cost_usd = 0.0
        #: Transient upstream failures that were retried. Counted rather than hidden: a row whose
        #: decisions each needed a retry is a different measurement from one whose did not, and
        #: nothing else in the record would show it.
        self.transient_retries = 0

    def _system(self) -> str:
        system = _SYSTEM
        if not self.issue_directives:
            # Cut the directive-issuing half of the shared prompt, leaving everything else
            # identical. Two prompts that differ in one section are comparable; two written
            # separately are not.
            system = _strip_issuing(system)
        return system + _JSON_INSTRUCTION

    def decide(self, world_view: WorldView) -> Orders:
        self.calls.append(world_view)
        argv = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--system-prompt",
            self._system(),
        ]
        if self.model:
            argv += ["--model", self.model]

        payload = world_view.model_dump_json()
        attempts = transient_attempts()
        for attempt in range(attempts):
            try:
                done = subprocess.run(
                    argv,
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                _record_provider_call("failure", "timeout")
                raise BrainError(f"claude -p timed out after {self.timeout}s") from exc
            except OSError as exc:
                _record_provider_call("failure", "exec")
                raise BrainError(f"could not run claude: {exc}") from exc

            if done.returncode == 0:
                break

            why = _why_it_failed(done)
            # A failure the service calls temporary must not become a permanent `safe fallback`
            # in the row's record on the first try. Anything else fails immediately: retrying a
            # bad model or a quota wall spends a blocked game thread on a certain failure.
            if attempt < attempts - 1 and _is_transient(why):
                _record_provider_call("failure", _failure_class(why))
                self.transient_retries += 1
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            _record_provider_call("failure", _failure_class(why))
            raise BrainError(f"claude -p exited {done.returncode}: {why}")

        try:
            envelope = json.loads(done.stdout)
        except ValueError as exc:
            raise BrainError(f"claude -p did not return JSON: {done.stdout[:200]}") from exc
        if envelope.get("is_error"):
            _record_provider_call("failure", _failure_class(str(envelope.get("result") or "")))
            raise BrainError(f"claude -p reported an error: {envelope.get('result')!r:.200}")

        _record_provider_call("success")
        self.cost_usd += float(envelope.get("total_cost_usd") or 0.0)
        orders = _parse_orders(str(envelope.get("result") or ""))
        if orders is None:
            self.malformed += 1
            raise BrainError("could not find orders JSON in the reply")
        return orders


def _strip_issuing(system: str) -> str:
    """Remove the section that tells the model it may issue directives.

    Section-level rather than line-level: the prompt is written in ``##`` sections, so cutting
    on that boundary leaves a document that still reads as one piece. A prompt visibly mangled
    by its own ablation is a confound in the very measurement it exists to serve.
    """
    lines = system.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("## "):
            skipping = "directive" in line.lower() and "issu" in line.lower()
        if not skipping:
            out.append(line)
    return "\n".join(out)


def _parse_orders(text: str) -> Orders | None:
    """Pull an ``Orders`` out of a reply that was asked for bare JSON.

    Tolerant on purpose, and only in ways that cannot change the decision: a fenced block, or
    prose wrapped around the object, are formatting failures rather than reasoning ones, and
    throwing the decision away for them would measure the model's obedience rather than its
    play. What is *not* tolerated is guessing at a missing or unparseable ``choices`` — that is
    a real failure and it is counted.
    """
    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    brace = text.find("{")
    if brace >= 0:
        candidates.append(text[brace : text.rfind("}") + 1])
    candidates.append(text)

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(payload, dict) or "choices" not in payload:
            continue
        try:
            return Orders.model_validate(payload)
        except Exception:  # noqa: BLE001 — a shape we cannot use is the same as none
            continue
    return None


__all__ = ["ClaudeCodeBrain", "DEFAULT_MODEL"]
