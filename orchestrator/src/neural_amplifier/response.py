"""The shapes we ask a model to fill in, as distinct from the shape we pass around.

``Orders`` is the wire contract: tolerant of unknown fields, carrying everything a decision
produces, shared with the adapter. That is the right design for a type that crosses a boundary
and the wrong one to hand to structured output, for two reasons discovered the hard way.

**A tolerant schema is an unbounded grammar.** Every contract model inherits ``extra="allow"``,
which becomes ``additionalProperties: true`` — fine for parsing a world view from an engine that
grew a field, ruinous for a constrained-decoding grammar that now has to admit arbitrary JSON at
every level.

**Size is a hard limit, not a soft cost.** Adding nested ``Directive`` objects to ``Orders`` took
its schema to 4,987 bytes and the API began rejecting every request with ``Schema is too
complex`` and ``Grammar compilation timed out``. Invariant 9 then did exactly what it should:
every decision degraded to the native answer and the run completed looking fine, with the
stability harness reporting a perfect 1.00 for ten identical fallbacks. Measured: 4,987 bytes
fails, 1,815 passes, 855 passes.

So the response models here are strict, short, and **scoped to what the decision is actually
allowed to do**. That last part is not a workaround — it is the rule the design already stated:
a per-base production choice has no business setting faction policy. Asking it to fill in a
directive schema was always wrong; it was merely also expensive.

Field descriptions stay, in compressed form. They are load-bearing: with structured output the
model reads the JSON schema, and explaining ``cited`` only in the system prompt left it empty on
every measured run.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contract import Comparator, Orders, Scope


class _Strict(BaseModel):
    """No unknown fields, and no class docstrings in the generated schema.

    Two separate savings, both necessary. ``extra="forbid"`` keeps the grammar bounded — see the
    module docstring. Dropping the model-level description keeps it *small*: Pydantic emits every
    class docstring into the schema, so the explanations these classes carry for maintainers were
    being paid for on every single request. Field descriptions are kept, because those are the
    ones the model actually reads.
    """

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
        schema = handler(core_schema)
        schema.pop("description", None)
        return schema


class ChoiceOut(_Strict):
    action_id: str = Field(description="An `id` from action_space.")
    reason: str | None = Field(default=None, description="One or two concrete sentences.")


class DirectiveOut(_Strict):
    """A directive as a model may write it.

    Deliberately missing ``baseline`` and ``issued_turn``, which :func:`directives.accept` stamps
    from the world view that issued it. Those are observations, not judgements — the model was
    shown the number, and asking it to repeat one it has already read is only a chance to
    paraphrase it wrong.
    """

    id: str = Field(description="Short kebab-case id.")
    intent: str = Field(description="One sentence: what this commits us to, and why.")
    metric: str = Field(description="One of the metric names listed in the prompt.")
    comparator: Comparator
    priority: int = Field(default=5, description="1-10; 9-10 survival, 7-8 committed.")
    target: float | None = None
    entities: list[str] = Field(
        default_factory=list,
        description="Grounding fact ids this is about; how a later decision finds it.",
    )
    horizon_turn: int | None = Field(default=None, description="Turn it should be met by.")
    # No ``rationale``. ``Directive`` keeps the field for hand-written plans, but asking a model
    # for both a one-sentence intent and a separate rationale buys a near-duplicate string at the
    # schema's most expensive point — and a later decision reads ``intent`` either way.


class TacticalOut(_Strict):
    """What a base- or unit-scope decision may return.

    No ``directives``: these fire once per base or unit per turn and exist to make one concrete
    move well. Letting them set faction policy would be both wrong and, at this frequency, the
    most expensive schema in the system.
    """

    choices: list[ChoiceOut] = Field(default_factory=list)
    notes: str | None = None
    cited: list[str] = Field(
        default_factory=list,
        description="Ids of grounding facts that changed your assessment. Never invent one.",
    )
    followed: list[str] = Field(
        default_factory=list, description="Ids of directives that changed this choice."
    )
    overrode: list[str] = Field(
        default_factory=list,
        description="Ids of directives you knowingly worked against; say why in reason.",
    )


class StrategicOut(TacticalOut):
    """What a faction-scope decision may return: the same, plus the power to set direction."""

    directives: list[DirectiveOut] = Field(
        default_factory=list,
        description="Standing intent for FUTURE decisions. Empty unless setting direction.",
    )


def model_for(scope: Scope) -> type[TacticalOut]:
    """The response schema a decision at this scope is allowed to fill in.

    Scope rather than surface id: the registry has 77 surfaces and the rule is about authority,
    not identity. Faction-scope decisions are the low-frequency ones that reason over a path;
    everything else makes one move.
    """
    return StrategicOut if scope == "turn" else TacticalOut


def to_orders(parsed: Any) -> Orders:
    """Widen a response back onto the wire contract.

    One direction only. The strict model exists to constrain generation; everything downstream —
    validation, the guard, the record — speaks ``Orders`` and should not learn that two response
    shapes exist.
    """
    payload = parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else dict(parsed)
    return Orders.model_validate(payload)
