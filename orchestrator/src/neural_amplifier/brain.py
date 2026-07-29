"""The brain seam.

:class:`Brain` is the whole interface between the orchestrator and whatever
produces a decision. :class:`ScriptedBrain` is the fake used by every test —
deterministic and free. :class:`ClaudeBrain` is the real one; real API calls are
opt-in (``docs/building-and-testing.md`` §1), so the ``anthropic`` import is
lazy and lives in the optional ``claude`` extra.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from .contract import Choice, Orders, WorldView

#: Default model. Chosen deliberately — see docs/observability.md for the cost
#: and latency signals that should drive any change here.
DEFAULT_MODEL = "claude-opus-5"


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

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "high") -> None:
        self.model = model
        self.effort = effort

    def decide(self, world_view: WorldView) -> Orders:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only with the extra
            raise BrainError(
                "the 'claude' extra is not installed; run `uv sync --extra claude`"
            ) from exc

        client = anthropic.Anthropic()
        try:
            response = client.messages.parse(
                model=self.model,
                max_tokens=16000,
                output_format=Orders,
                output_config={"effort": self.effort},
                system=_SYSTEM,
                messages=[{"role": "user", "content": world_view.model_dump_json()}],
            )
        except Exception as exc:  # pragma: no cover - network path
            raise BrainError(str(exc)) from exc

        # Safety classifiers can decline; that is a content outcome, not an error.
        if response.stop_reason == "refusal":  # pragma: no cover - network path
            raise BrainError("model declined the request")

        parsed = response.parsed_output
        if parsed is None:  # pragma: no cover - network path
            raise BrainError("model returned no parseable orders")
        return Orders.model_validate(parsed)


_SYSTEM = """You are playing a faction in Sid Meier's Alpha Centauri.

You will receive a world view as JSON. Choose from `action_space` and return
orders. Every `action_id` you return MUST appear in the world view's
`action_space` — the engine is authoritative and an action you invent will be
rejected. Give a short, concrete reason for each choice.

If the world view carries a `fairness` block with handicaps, those are rule
advantages you actually hold. Reason about them honestly rather than ignoring
them."""
