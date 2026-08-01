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
import re
import subprocess

from .brain import _SYSTEM, BrainError, DEFAULT_MODEL
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

        try:
            done = subprocess.run(
                argv,
                input=world_view.model_dump_json(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise BrainError(f"claude -p timed out after {self.timeout}s") from exc
        except OSError as exc:
            raise BrainError(f"could not run claude: {exc}") from exc

        if done.returncode != 0:
            raise BrainError(f"claude -p exited {done.returncode}: {done.stderr[:300]}")

        try:
            envelope = json.loads(done.stdout)
        except ValueError as exc:
            raise BrainError(f"claude -p did not return JSON: {done.stdout[:200]}") from exc
        if envelope.get("is_error"):
            raise BrainError(f"claude -p reported an error: {envelope.get('result')!r:.200}")

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
