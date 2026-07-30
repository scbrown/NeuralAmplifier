"""The brain seam.

:class:`Brain` is the whole interface between the orchestrator and whatever
produces a decision. :class:`ScriptedBrain` is the fake used by every test —
deterministic and free. :class:`ClaudeBrain` is the real one; real API calls are
opt-in (``docs/building-and-testing.md`` §1), so the ``anthropic`` import is
lazy and lives in the optional ``claude`` extra.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .contract import Choice, Orders, WorldView
from .metrics import vocabulary_prompt
from .response import model_for, to_orders

#: Default model. Chosen deliberately — see docs/observability.md for the cost
#: and latency signals that should drive any change here.
#: Haiku 4.5 by design, not by cost-cutting reflex: it is the model this project is authorised to
#: spend on, and a measured comparison found it reaches the SAME base.production choice as Opus 5
#: with identical stability (1.00 over 5 runs each). It reads less of the grounding on the way
#: there — utilisation 0.17 against 0.46 — which is a retrieval finding (na-373), not a reason to
#: pay for a larger model. Override per run with NA_BRAIN_MODEL.
DEFAULT_MODEL = os.environ.get("NA_BRAIN_MODEL") or "claude-haiku-4-5"

#: Model for **non-gameplay** inference — K3's postgame extraction of `mem:`
#: episodes from a decision log, and anything else that summarises rather than
#: plays. Deliberately a separate, cheaper dial from the brain's: extraction is
#: bulk, offline, and tagged as *learned* rather than canonical, so it does not
#: need the model that has to win a game. Note the datalinks ingester (K1) uses
#: **no** model at all — parsing a fixed-arity CSV is deterministic work, and a
#: hallucinated tech prerequisite tagged `canonical` is the worst failure
#: available to us (``docs/knowledge-architecture.md`` §Why extraction uses no
#: model).
EXTRACTION_MODEL = os.environ.get("NA_EXTRACTION_MODEL") or "claude-haiku-4-5"


class BrainError(RuntimeError):
    """The brain could not produce a decision. The caller degrades safely."""


class Brain(Protocol):
    """Takes a world view, returns orders. That is the entire contract."""

    name: str

    def decide(self, world_view: WorldView) -> Orders: ...


class ScriptedBrain:
    """A deterministic fake.

    Default behaviour picks the safe fallback action, which makes it a useful
    baseline: a test that passes with the scripted brain is testing the
    orchestrator, not the model.

    Pass ``responses`` to script a sequence (including malformed or illegal
    ones — that is the point), or ``chooser`` for a rule.
    """

    name = "scripted"

    def __init__(
        self,
        responses: Sequence[Orders] | None = None,
        chooser: Callable[[WorldView], Orders] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._chooser = chooser
        self._raises = raises
        self.calls: list[WorldView] = []

    def decide(self, world_view: WorldView) -> Orders:
        self.calls.append(world_view)
        if self._raises is not None:
            raise self._raises
        if self._responses:
            return self._responses.pop(0)
        if self._chooser is not None:
            return self._chooser(world_view)
        fallback = world_view.fallback_action_id()
        if fallback is None:
            return Orders(choices=[])
        return Orders(choices=[Choice(action_id=fallback, reason="scripted default")])


class ClaudeBrain:
    """The real brain.

    Requires the ``claude`` extra (``uv sync --extra claude``) and credentials.
    Nothing in the default test suite touches this path.
    """

    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, effort: str | None = "high") -> None:
        self.model = model
        #: None omits output_config entirely. Not every model accepts effort — it is rejected
        #: outright on Haiku 4.5 and Sonnet 4.5 — and sending it unconditionally turned every
        #: decision into a degraded fallback with a 400 that the orchestrator dutifully absorbed.
        self.effort = effort

    def _supports_effort(self, client: Any) -> bool:
        """Ask the API whether this model takes THIS effort level.

        Queried rather than hardcoded as a model list, because such a list is wrong the moment a
        model ships. Cached per instance: the answer cannot change for a fixed model id.

        The per-level flag, not the coarse ``effort.supported``: support is not all-or-nothing.
        Opus 4.5 accepts low/medium/high but rejects xhigh/max, so a model that "supports effort"
        can still 400 on the level we happen to ask for.

        A probe that cannot answer returns False, which omits effort and costs at most a
        default-effort request. Sending an unsupported level costs the entire decision — that is
        what turned five Haiku 4.5 runs into five degraded fallbacks.
        """
        cached = getattr(self, "_effort_ok", None)
        if cached is not None:
            return bool(cached)
        ok = False
        try:
            caps = client.models.retrieve(self.model).capabilities
            # Pydantic model in the SDK, plain dict in tests and fakes — support both rather than
            # making the probe's correctness depend on which one it was handed.
            effort = caps["effort"] if isinstance(caps, dict) else caps.effort
            level = (
                effort[self.effort]
                if isinstance(effort, dict)
                else getattr(effort, str(self.effort))
            )
            ok = bool(level["supported"] if isinstance(level, dict) else level.supported)
        except Exception:  # noqa: BLE001 — a capability probe must never fail a decision
            ok = False
        self._effort_ok = ok
        return ok

    def _parse(self, client: Any, world_view: WorldView, kwargs: dict[str, Any]) -> Any:
        """One call, retried once on a transient structured-output failure.

        ``Grammar compilation timed out`` appeared on 1 run in 10 after ``Orders`` grew nested
        directive objects — the schema got heavier and the server-side grammar build occasionally
        misses its deadline. It is not a bad request: the identical payload succeeds on a second
        attempt, because the compile is cached.

        Worth a retry specifically because the alternative is invariant 9 doing its job — the turn
        degrades to the native answer, which is correct behaviour and a silently worse decision.
        Only this one error class is retried; a genuine 400 should fail loudly and immediately.
        """
        attempts = 2
        for attempt in range(attempts):
            try:
                return client.messages.parse(
                    model=self.model,
                    max_tokens=16000,
                    output_format=model_for(world_view.scope),
                    system=_SYSTEM,
                    messages=[{"role": "user", "content": world_view.model_dump_json()}],
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001 — re-raised below unless retryable
                if attempt + 1 >= attempts or "grammar compilation" not in str(exc).lower():
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def decide(self, world_view: WorldView) -> Orders:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only with the extra
            raise BrainError(
                "the 'claude' extra is not installed; run `uv sync --extra claude`"
            ) from exc

        client = anthropic.Anthropic()
        try:
            kwargs: dict[str, Any] = {}
            if self.effort and self._supports_effort(client):
                kwargs["output_config"] = {"effort": self.effort}
            response = self._parse(client, world_view, kwargs)
        except Exception as exc:  # pragma: no cover - network path
            raise BrainError(str(exc)) from exc

        # Safety classifiers can decline; that is a content outcome, not an error.
        if response.stop_reason == "refusal":  # pragma: no cover - network path
            raise BrainError("model declined the request")

        parsed = response.parsed_output
        if parsed is None:  # pragma: no cover - network path
            raise BrainError("model returned no parseable orders")
        return to_orders(parsed)


_SYSTEM_TEMPLATE = """You are playing a faction in Sid Meier's Alpha Centauri.

You will receive a world view as JSON. Choose from `action_space` and return
orders. Every `action_id` you return MUST appear in the world view's
`action_space` — the engine is authoritative and an action you invent will be
rejected. Give a short, concrete reason for each choice.

If the world view carries a `fairness` block with handicaps, those are rule
advantages you actually hold. Reason about them honestly rather than ignoring
them.

If the world view carries a `grounding` list, each entry is a retrieved fact in
the form `<id> <text>` — for example
`unit:colony-pod Colony Pod; founds a new base elsewhere`. These are the game's
own rules, and a `[house-rule]` tag means the fact comes from a mod rather than
the base game.

Populate `cited` with the ids of the facts that actually influenced your
decision. Include a fact if reading it changed your assessment of an option,
whether it supported the option you chose OR helped you rule one out. Do not
include a fact that made no difference, and never invent an id you were not
given — `cited` is how we measure whether retrieval was worth its cost, so
padding it destroys the measurement it exists for.

## Standing directives

`directives` is the faction's standing plan: decisions made earlier that later
turns are supposed to serve. Each entry carries the directive, its `priority`
(1-10, higher matters more), and `current` — what its metric actually reads
right now — so you can see whether it is being met. `satisfied: null` means the
metric was not reported this turn and the directive cannot be checked here;
treat that as missing information, not as compliance.

Each entry also carries `via` and `hop`, which say how it reached you. `hop: 0`
means this decision directly moves what the directive is about. A higher hop
means it was reached through another directive — `fund-weather-paradigm →
fac:the-weather-paradigm` means the plan you are about to affect is itself
serving that project. Read the chain: it is why the resource you are spending is
not merely quantity but something already committed.

`tradeoffs` tells you what each option would cost the plan: for an
`action_id`/`directive_id` pair it gives the `delta` to the metric, the
`projected` value after acting, whether that `would_violate` a directive
currently being met, and `setback_turns` where a rate is known.

A directive is not an order. Weigh its `priority` against how much THIS
decision matters:

- 9-10 survival; overriding needs an immediate, concrete threat.
- 7-8 a committed plan; break it only for something urgent.
- 4-6 a preference worth real cost.
- 1-3 a tie-breaker.

So a priority-7 directive to save energy should lose to stopping a base from
falling, and beat finishing a Scout Patrol two turns sooner. When you do
override one, say why in the choice's `reason` — name the directive and what you
judged more valuable.

Populate `followed` with the ids of directives that changed what you picked, and
`overrode` with those you knowingly worked against. Overriding is allowed and
often correct; recording it is how we learn a directive was mispriced. As with
`cited`, never invent an id.

## Issuing directives

Leave `directives` in your response empty unless this decision genuinely sets
direction for future turns — a tech path, a social model, a saving plan. Most
decisions should not issue any.

A directive must be measurable, so `metric` must be one of these names exactly.
Anything else is discarded, because it could never be checked:

{vocabulary}

Use `at_least`/`at_most` with a `target` for an absolute bound, or
`increase`/`decrease`/`hold` to be measured against the value at the time you
issue it. Set `horizon_turn` to the turn by which it should be achieved — a plan
with no deadline cannot fail, and one that cannot fail teaches us nothing. Put
the reasoning in `intent`, in one sentence, because a later decision will read
it without the context you have now.

Set `entities` to the grounding fact ids the directive is about — the same ids
you would put in `cited`, e.g. `fac:the-planetary-transit-system` for a plan to
fund that project. This is how the directive is found again: a game can carry
hundreds of them, so a later decision is shown only the ones touching what it is
deciding on. A directive with no entities is still found through its `metric`,
which names the resource at stake, but one that is about a specific project and
does not name it may never be shown to the decision that could serve it."""


#: ``replace`` rather than ``format``: the prompt is prose with punctuation in it, and a stray
#: brace should not be able to turn a system prompt into a KeyError at decision time.
#:
#: Built from the vocabulary rather than written out, so a metric added to :mod:`.metrics` is
#: immediately something a model may write a directive against. The two drifting apart is the
#: one failure that would make every issued directive get rejected for naming a metric that
#: does exist.
_SYSTEM = _SYSTEM_TEMPLATE.replace("{vocabulary}", vocabulary_prompt())
