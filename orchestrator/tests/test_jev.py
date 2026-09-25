"""Advisory failures cannot choose a brain, strip orders, or fake eval coverage."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Directive, DirectiveStatus
from neural_amplifier.jev import CliCaller, JevGuard, from_env, tier_observation
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.policy import SurfacePolicy


class Fake:
    def __init__(self, answer=None, error=None):
        self.answer = answer or {"choice": "bigger-model", "confidence": 0.8, "noul": 0.99}
        self.error = error
        self.states = []

    def ask(self, kind, state, question, criteria=None):
        self.states.append(state)
        if self.error:
            raise self.error
        return {"answers": {"q": self.answer}, "model": "fixture", "usage": {"input_tokens": 1}}


def active(view):
    return view.model_copy(
        update={
            "directives": [
                DirectiveStatus(
                    directive=Directive(
                        id="save",
                        intent="Save energy",
                        metric="energy_reserves",
                        comparator="at_least",
                        target=100,
                    )
                )
            ]
        }
    )


def test_guard_warns_but_preserves_orders_and_rationale(thinker_base):
    view = active(thinker_base)
    baseline = Orchestrator(ScriptedBrain()).decide(view)
    observed = Orchestrator(ScriptedBrain(), guard=JevGuard(Fake())).decide(view)
    assert observed.orders == baseline.orders
    assert observed.record.knowledge.hank_verdict == "warn"
    assert not observed.record.knowledge.stripped
    assert "fixture" in observed.record.knowledge.advisories[0]


@pytest.mark.parametrize("answer", [{"noul": None}, {"noul": float("nan")}, {"noul": 2}])
def test_bad_guard_answer_is_unavailable_not_clean(thinker_base, answer):
    result = Orchestrator(ScriptedBrain(), guard=JevGuard(Fake(answer))).decide(
        active(thinker_base)
    )
    assert result.orders.choices
    assert result.record.knowledge.hank_degraded
    assert result.record.knowledge.hank_verdict == "allow"


def test_guard_without_directive_makes_no_call(thinker_base):
    caller = Fake()
    Orchestrator(ScriptedBrain(), guard=JevGuard(caller)).decide(thinker_base)
    assert caller.states == []


def test_routing_is_logged_without_changing_brain_or_order(thinker_base):
    baseline = Orchestrator(ScriptedBrain()).decide(thinker_base)
    result = Orchestrator(ScriptedBrain(), jev=Fake()).decide(thinker_base)
    assert result.orders == baseline.orders
    assert result.record.tier == baseline.record.tier
    assert result.record.jev_tier["suggestion"] == "bigger-model"
    assert result.record.jev_tier["acted_on"] is False
    assert baseline.record.jev_tier is None


def test_routing_failure_preserves_decision(thinker_base):
    result = Orchestrator(ScriptedBrain(), jev=Fake(error=TimeoutError())).decide(thinker_base)
    assert result.orders.choices
    assert result.record.jev_tier["status"] == "unavailable"
    assert not result.record.degraded


def test_none_option_is_recorded(thinker_base):
    result = tier_observation(Fake({"choice": "none-of-these", "confidence": 0.9}), thinker_base)
    assert result["suggestion"] == "none-of-these"
    assert result["acted_on"] is False


def test_cli_deadline_and_no_shell(tmp_path):
    script = tmp_path / "client.py"
    script.write_text("import time\ntime.sleep(1)\n")
    with pytest.raises(subprocess.TimeoutExpired):
        CliCaller((sys.executable, str(script)), timeout=0.02).ask("noul", {}, "question")


def test_cli_preserves_request_usage_and_literal_state(tmp_path):
    script = tmp_path / "client.py"
    script.write_text(
        'import json\nprint(json.dumps({"answers":{"q":{"noul":0.2}},"usage":{"input_tokens":7},"model":"fixture"}))\n'
    )
    state = {"fact": "$(touch never) `echo never`"}
    result = CliCaller((sys.executable, str(script))).ask("noul", state, "question")
    assert result["request"]["state"] == state
    assert result["usage"]["input_tokens"] == 7


def test_config_is_explicit_and_bounded(monkeypatch):
    monkeypatch.delenv("NA_JEV_COMMAND", raising=False)
    assert from_env() is None
    monkeypatch.setenv("NA_JEV_COMMAND", '["python3", "jev.py"]')
    monkeypatch.setenv("NA_JEV_TIMEOUT", "nan")
    with pytest.raises(ValueError):
        from_env()


def test_jev_eval_pin_validation_and_third_arm(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
    import jev_ranking
    import multi_decision_ranking as ranking

    specs = ranking.decisions()
    pin = tmp_path / "jev-scores.json"
    jev_ranking.harvest(pin, specs, Fake({"score": 1.5}))
    assert jev_ranking.load(pin, specs) == {n: s["grounding"] for n, s in specs.items()}
    monkeypatch.setattr(ranking, "PINNED", tmp_path / "grounding.json")
    ranking.PINNED.write_text(json.dumps(specs))
    arms = ranking.arms()
    assert len(arms) == 3 * len(specs)
    assert all(f"{name}.jev" in arms for name in specs)
    # A changed fact must fail closed, not quietly reuse the old ranking.
    specs[next(iter(specs))]["grounding"][0] += " changed"
    with pytest.raises(ValueError, match="stale"):
        jev_ranking.load(pin, specs)


def test_jev_eval_reuses_dominance_gate(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
    import jev_ranking
    import multi_decision_ranking as ranking

    specs = ranking.decisions()
    monkeypatch.setattr(ranking, "PINNED", tmp_path / "grounding.json")
    ranking.PINNED.write_text(json.dumps(specs))
    jev_ranking.harvest(tmp_path / "jev-scores.json", specs, Fake({"score": 1}))
    name, spec = next(iter(specs.items()))
    row = json.dumps({"choice": "build:0", "cited": [spec["grounding"][0].split()[0]]})
    (tmp_path / f"{name}.all.answers.jsonl").write_text((row + "\n") * 6)
    ranking.score(tmp_path)
    report = capsys.readouterr().out
    assert report.count("REFUSING THE HEADLINE") == 2
    assert "VERDICT:" not in report


def test_deterministic_surface_stays_deterministic(thinker_base):
    view = thinker_base.model_copy(update={"surface_id": "base.production"})
    result = Orchestrator(
        ScriptedBrain(), jev=Fake(), policy=SurfacePolicy(default=False, source=Path("test"))
    ).decide(view)
    assert result.record.tier == "deterministic"
    assert result.record.jev_tier["suggestion"] == "bigger-model"


def test_service_wires_optional_guard_and_tier(monkeypatch):
    from neural_amplifier import jev
    from neural_amplifier.hank import GuardChain
    from neural_amplifier.service import create_app

    fake = Fake()
    monkeypatch.setattr(jev, "from_env", lambda: fake)
    monkeypatch.setenv("NA_CONFIG", "/nonexistent/na.toml")
    app = create_app(brain=ScriptedBrain(), sinks=[])
    assert app.state.orchestrator.jev is fake
    assert isinstance(app.state.orchestrator.guard, GuardChain)
