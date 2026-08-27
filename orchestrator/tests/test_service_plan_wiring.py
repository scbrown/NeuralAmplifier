"""The served orchestrator must actually build the standing plan it was configured with.

This exists because the knob was declared and consumed by nothing. `config.run.plan` was read
from `NA_PLAN` and never reached `Orchestrator`, so a served run always had `plan=None`:
`/agent/directive` replied *"attached; it takes effect when you submit this decision"*, the
decision succeeded, and the directive went nowhere. Every record from a real game carried
`plan_absent: true` — which reads as "no plan was configured" rather than "the configuration was
ignored", and that is why it survived (na-43h).

Measured on a live game at turn 45 before the fix: an agent issued `bank-for-expansion` from a
real `base.production` decision, received the success response with a baseline stamped at 181,
and the plan file was never created.

The whole existing directive suite passes an Orchestrator its store directly, which is why none
of it caught this. So these tests deliberately go through `create_app` — the seam that was
broken is the wiring, not the behaviour, and only the factory exercises it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_amplifier import service


def test_a_configured_plan_reaches_the_served_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression itself. `plan_absent` is the field that lied, so assert the store rather
    than the flag — a future refactor could set the flag correctly and still not persist."""
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("NA_PLAN", str(plan))
    app = service.create_app()
    assert app.state.orchestrator.plan is not None
    assert app.state.orchestrator.plan.path == plan


def test_no_configured_plan_still_means_no_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running with no standing plan is legitimate and is what `plan_absent` is for. Wiring the
    config must not invent a store nobody asked for — an empty plan and an absent one call for
    different readings in a record."""
    monkeypatch.delenv("NA_PLAN", raising=False)
    app = service.create_app()
    assert app.state.orchestrator.plan is None


def test_a_directive_issued_through_the_service_survives_the_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the wiring: the store on the served orchestrator is the one that gets
    written, so a directive issued on one decision is in force for the next.

    Asserting the file rather than an in-memory object is deliberate. The live failure was that
    the success response and the persisted state disagreed, and only the file can tell them apart.
    """
    from neural_amplifier.contract import Directive

    plan = tmp_path / "plan.json"
    monkeypatch.setenv("NA_PLAN", str(plan))
    app = service.create_app()
    store = app.state.orchestrator.plan
    assert store is not None

    store.add(
        [
            Directive(
                id="bank-for-expansion",
                intent="Hold a working reserve so a pod can be hurried the turn it matters.",
                metric="energy_reserves",
                comparator="at_least",
                target=200,
                priority=6,
                issued_turn=45,
                baseline=181.0,
            )
        ]
    )
    assert plan.is_file(), "a configured store must persist to the configured path"
    written = json.loads(plan.read_text())
    assert [d["id"] for d in written["directives"]] == ["bank-for-expansion"]
    assert [d.id for d in store.in_force(46)] == ["bank-for-expansion"]


def test_service_keeps_a_seed_plan_immutable_when_state_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from neural_amplifier.contract import Directive

    seed = tmp_path / "seed.json"
    state = tmp_path / "runtime" / "plan.json"
    payload = {
        "directives": [
            Directive(
                id="expand",
                intent="Expand without rewriting this experiment fixture.",
                metric="base_count",
                comparator="at_least",
                target=10,
            ).model_dump(mode="json")
        ]
    }
    seed.write_text(json.dumps(payload, indent=2) + "\n")
    original = seed.read_bytes()
    monkeypatch.setenv("NA_PLAN", str(seed))
    monkeypatch.setenv("NA_PLAN_STATE", str(state))

    app = service.create_app()
    store = app.state.orchestrator.plan
    assert store is not None
    store.add(
        [
            Directive(
                id="runtime",
                intent="Remember a decision made during this run.",
                metric="energy_reserves",
                comparator="at_least",
                target=100,
            )
        ]
    )

    assert seed.read_bytes() == original
    assert {d["id"] for d in json.loads(state.read_text())["directives"]} == {
        "expand",
        "runtime",
    }
