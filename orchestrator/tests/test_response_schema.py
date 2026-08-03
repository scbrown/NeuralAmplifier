"""The response schema must stay small enough for the API to compile a grammar from it.

This file exists because of a failure the entire rest of the suite missed. Adding nested
``Directive`` objects to ``Orders`` took its JSON schema to 4,987 bytes, and the API began
rejecting every request with ``Schema is too complex`` / ``Grammar compilation timed out``.
Invariant 9 then worked exactly as designed: every decision degraded to the native answer, the
run completed, and the stability harness reported 1.00 for ten identical fallbacks.

Nothing caught it because every brain test uses a fake client — which is right for testing our
logic and useless for testing whether a real API will accept the schema we generate. So the
guard is a size budget, checkable offline, on the one property that broke.

Measured on the live API: 4,987 bytes fails, 1,815 passes, 855 passes.
"""

from __future__ import annotations

import json

from neural_amplifier.contract import Scope
from neural_amplifier.response import StrategicOut, TacticalOut, model_for, to_orders

#: Well under the smallest schema observed to fail, with room for a field or two. Not derived
#: from a documented limit — none is published — so it is a tripwire, not a specification.
BUDGET_BYTES = 2500


def _size(model: type) -> int:
    return len(json.dumps(model.model_json_schema()))


def test_the_tactical_schema_stays_within_budget() -> None:
    assert _size(TacticalOut) < BUDGET_BYTES


def test_the_strategic_schema_stays_within_budget() -> None:
    """The one that broke. Directives live here and nowhere else."""
    assert _size(StrategicOut) < BUDGET_BYTES


def test_response_models_forbid_unknown_fields() -> None:
    """``extra="allow"`` becomes ``additionalProperties: true``, which is an unbounded grammar.

    Right for parsing a world view from an engine that grew a field; ruinous for constrained
    decoding, which then has to admit arbitrary JSON at every level.
    """
    for model in (TacticalOut, StrategicOut):
        schema = model.model_json_schema()
        assert schema.get("additionalProperties") is False, model.__name__
        for name, definition in schema.get("$defs", {}).items():
            assert definition.get("additionalProperties") is False, name


def test_only_faction_scope_decisions_may_issue_directives() -> None:
    """Not a workaround for the size limit — the rule the design already stated.

    A per-base production choice has no business setting faction policy. Asking it to fill in a
    directive schema was always wrong; it was merely also the most expensive schema in the system,
    on the highest-frequency path.
    """
    assert model_for("turn") is StrategicOut
    for scope in ("base", "unit"):
        assert model_for(scope) is TacticalOut  # type: ignore[arg-type]
        assert "directives" not in model_for(scope).model_fields


def test_every_scope_in_the_contract_maps_to_a_model() -> None:
    for scope in Scope.__args__:  # type: ignore[attr-defined]
        assert issubclass(model_for(scope), TacticalOut)


def test_a_response_widens_onto_the_wire_contract() -> None:
    """Downstream speaks Orders and must not learn that two response shapes exist."""
    parsed = StrategicOut.model_validate(
        {
            "choices": [{"action_id": "tech:48", "reason": "ecology path"}],
            "cited": ["tech:48"],
            "followed": ["fund-weather-paradigm"],
            "overrode": [],
            "directives": [
                {
                    "id": "ecology-first",
                    "intent": "Take the ecology line before industry.",
                    "metric": "labs_output",
                    "comparator": "increase",
                    "priority": 7,
                    "entities": ["fac:the-weather-paradigm"],
                }
            ],
        }
    )
    orders = to_orders(parsed)

    assert orders.choices[0].action_id == "tech:48"
    assert orders.directives[0].id == "ecology-first"
    assert orders.followed == ["fund-weather-paradigm"]


def test_the_model_cannot_set_fields_the_orchestrator_owns() -> None:
    """``baseline`` and ``issued_turn`` are observations, stamped from the world view.

    The model was shown the number; asking it to repeat one it has already read is only a chance
    to paraphrase it wrong.
    """
    fields = StrategicOut.model_fields["directives"].annotation
    directive_out = fields.__args__[0]  # type: ignore[union-attr]
    assert "baseline" not in directive_out.model_fields
    assert "issued_turn" not in directive_out.model_fields


def test_a_tactical_response_still_reports_what_it_followed() -> None:
    """Tactical decisions cannot SET a plan, but the whole point is that they serve one."""
    for field in ("cited", "followed", "overrode"):
        assert field in TacticalOut.model_fields


def test_the_directive_field_states_WHEN_to_issue_not_only_when_not_to() -> None:
    """na-1gl: the model reads THIS schema, so the trigger condition has to live here.

    It read "Standing intent for FUTURE decisions. Empty unless setting direction." — nine words
    whose only actionable clause pointed at empty. Twenty runs of faction.tech issued zero
    directives, including the ten invited to in the system prompt (na-j2w).

    The trigger existed in ``contract.Orders.directives``, but that is the wire contract — "the
    shape we pass around" — and the model never sees it. Same failure as ``cited``, which stayed
    empty while its explanation sat in the system prompt.

    So this asserts the description is not PURELY a suppressor: it must say when a directive is
    called for, not only when to omit one. Pinned because the bug already recurred once, in the
    same file, by the same route.
    """
    field = StrategicOut.model_fields["directives"]
    description = (field.description or "").lower()

    assert description, "the model-facing directives field has no description at all"
    # The suppressor is deliberate and must survive — without it a faction decision emits policy
    # every pass, which is the opposite failure.
    assert "empty" in description, "the suppressor was dropped; expect directive spam"
    # ...but it must not be the ONLY instruction. Something has to state the positive trigger.
    assert "binds later turns" in description or "commits" in description, (
        "the description tells the model when NOT to issue and never when to. That is the na-1gl "
        f"defect verbatim: {field.description!r}"
    )


def test_the_directive_trigger_did_not_come_out_of_the_size_budget() -> None:
    """The na-1gl wording is only safe because ``directives`` exists on the faction-scope schema
    alone. This is the file whose whole reason for existing is that a too-large schema degraded
    every decision to fallback while the harness reported a clean 1.00 — so growing a description
    is checked, not assumed."""
    strategic = len(json.dumps(StrategicOut.model_json_schema()))
    tactical = len(json.dumps(TacticalOut.model_json_schema()))
    assert strategic < BUDGET_BYTES, f"strategic schema {strategic}b over budget {BUDGET_BYTES}b"
    assert tactical < BUDGET_BYTES, f"tactical schema {tactical}b over budget {BUDGET_BYTES}b"
    # The high-frequency schema must not have paid for this at all.
    assert "directives" not in TacticalOut.model_fields
