"""What a failing `claude -p` is allowed to tell us — na-bql / ladder-attempt4.

The failure path reported `done.stderr[:300]` and discarded stdout. MEASURED against the real
CLI on 2026-08-24 (`claude -p --output-format json --model <bad>`): stdout carried 1265 bytes of
parseable JSON whose `result` read "There's an issue with the selected model ... it may not exist
or you may not have access to it", while stderr carried 98 bytes of machine tag.

On ladder-attempt4 the live failures arrived with an EMPTY stderr, so 19 fallback decisions were
recorded as `degrade_reason="claude -p exited 1: "` — the cause named as the empty string — with
the explanation sitting unread in stdout. Rate limit, usage cap, timeout and crash are four
different remedies and that message distinguishes none of them.

These arms pin the ORDER as well as the content, because the order is the whole value: the
sentence a human can act on comes first.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from neural_amplifier.claude_code_brain import _why_it_failed


def _why() -> Any:
    return _why_it_failed


def _done(stdout: str, stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["claude"], returncode=1, stdout=stdout, stderr=stderr)


def test_the_json_result_is_preferred_because_it_is_the_actionable_sentence() -> None:
    """The shape actually measured from the CLI: JSON on stdout, machine tag on stderr."""
    stdout = json.dumps(
        {
            "is_error": True,
            "result": "There's an issue with the selected model (bogus-model). It may not exist "
            "or you may not have access to it.",
            "total_cost_usd": 0,
        }
    )
    stderr = '[claude-code:unrecognized_model] {"model":"bogus-model","query_source":"sdk"}'
    why = _why()(_done(stdout, stderr))
    assert why.startswith("result: There's an issue with the selected model")
    assert "stderr: [claude-code:unrecognized_model]" in why
    assert why.index("result:") < why.index("stderr:"), "the actionable sentence must come first"


def test_an_empty_stderr_no_longer_erases_the_cause() -> None:
    """The ladder-attempt4 case. Under the old code this rendered as nothing at all."""
    stdout = json.dumps({"is_error": True, "result": "Usage limit reached for this account."})
    why = _why()(_done(stdout, ""))
    assert why == "result: Usage limit reached for this account."
    assert why.strip() != ""


def test_unparseable_stdout_is_still_reported_verbatim() -> None:
    """The envelope shape may change; raw stdout beats silence."""
    why = _why()(_done("Segmentation fault (core dumped)", ""))
    assert why == "stdout: Segmentation fault (core dumped)"


def test_stderr_alone_is_used_when_stdout_is_empty() -> None:
    why = _why()(_done("", "connection reset by peer"))
    assert why == "stderr: connection reset by peer"


def test_both_streams_empty_says_so_explicitly() -> None:
    """ "No output" is a finding. It must not render as a trailing colon and nothing else."""
    why = _why()(_done("", ""))
    assert why == "both stdout and stderr were EMPTY"
    assert not why.endswith(":")


def test_the_message_is_bounded() -> None:
    """A brain error becomes a degrade_reason on every fallback record; it cannot be unbounded."""
    why = _why()(_done(json.dumps({"result": "x" * 5000}), "y" * 5000))
    assert len(why) < 700


# --------------------------------------------------------------------------------------
# Retrying a failure the service itself calls temporary — measured on ladder-attempt4.
# --------------------------------------------------------------------------------------

from neural_amplifier.claude_code_brain import _is_transient  # noqa: E402


def test_the_measured_529_is_recognised_as_transient() -> None:
    """The exact string the row produced, once the reason stopped being blank."""
    assert _is_transient(
        "result: API Error: 529 Overloaded. This is a server-side issue, usually temporary "
        "— try again in a moment."
    )


def test_a_quota_wall_is_NOT_retried() -> None:
    """Retrying one of these cannot succeed for hours; it spends a blocked game thread."""
    assert not _is_transient("result: Usage limit reached for this account.")
    assert not _is_transient("result: You have exceeded your quota.")
    # and it must lose even when a transient-looking token is also present
    assert not _is_transient("result: Usage limit reached. API Error: 529 seen earlier.")


def test_a_bad_model_is_NOT_retried() -> None:
    assert not _is_transient(
        "result: There's an issue with the selected model (bogus). It may not exist or you may "
        "not have access to it."
    )


def test_an_empty_reason_is_not_treated_as_transient() -> None:
    """The old blank message must not become a licence to retry everything."""
    assert not _is_transient("")
    assert not _is_transient("both stdout and stderr were EMPTY")


# --------------------------------------------------------------------------------------
# The LOOP, not just the predicate. Classifying 529 correctly is worth nothing if nothing
# retries on it — and a predicate test cannot tell the difference.
# --------------------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from neural_amplifier.claude_code_brain import ClaudeCodeBrain  # noqa: E402
from neural_amplifier.contract import WorldView  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

_A_529 = json.dumps(
    {
        "is_error": True,
        "result": "API Error: 529 Overloaded. This is a server-side issue, usually temporary.",
    }
)
_A_QUOTA = json.dumps({"is_error": True, "result": "Usage limit reached for this account."})


def _reply(action_id: str) -> str:
    return json.dumps(
        {
            "is_error": False,
            "total_cost_usd": 0.0,
            "result": json.dumps(
                {"choices": [{"action_id": action_id, "reason": "because"}], "directives": []}
            ),
        }
    )


def _world() -> WorldView:
    raw = (FIXTURES / "thinker_base_production.json").read_text()
    return WorldView.model_validate(json.loads(raw))


def _scripted(monkeypatch: Any, mod: Any, outcomes: list[tuple[int, str]]) -> list[int]:
    """Replace subprocess.run with a script of (returncode, stdout) and count the calls."""
    calls: list[int] = []

    def fake_run(*_a: Any, **_k: Any) -> subprocess.CompletedProcess:
        code, out = outcomes[len(calls)]
        calls.append(1)
        return subprocess.CompletedProcess(args=["claude"], returncode=code, stdout=out, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    return calls


def test_a_529_is_actually_RETRIED_and_the_second_attempt_is_used(monkeypatch: Any) -> None:
    """The regression that matters: one transient failure must not become a fallback."""
    from neural_amplifier import claude_code_brain as mod

    world = _world()
    action = world.action_space[0].id
    calls = _scripted(monkeypatch, mod, [(1, _A_529), (0, _reply(action))])
    brain = ClaudeCodeBrain()

    orders = brain.decide(world)

    assert len(calls) == 2, "the transient failure was not retried"
    assert brain.transient_retries == 1
    assert orders.choices[0].action_id == action


def test_the_retry_is_BOUNDED(monkeypatch: Any) -> None:
    """Two transient failures still fail, and are not retried a third time."""
    from neural_amplifier import claude_code_brain as mod
    from neural_amplifier.brain import BrainError

    calls = _scripted(monkeypatch, mod, [(1, _A_529), (1, _A_529)])
    brain = ClaudeCodeBrain()

    with pytest.raises(BrainError) as caught:
        brain.decide(_world())

    assert len(calls) == 2, "retried more times than the attempt budget allows"
    assert brain.transient_retries == 1
    assert "529" in str(caught.value), "the surviving error must still name the cause"


def test_a_quota_wall_is_not_retried_by_the_LOOP(monkeypatch: Any) -> None:
    """Predicate and loop must agree; a quota failure costs exactly one attempt."""
    from neural_amplifier import claude_code_brain as mod
    from neural_amplifier.brain import BrainError

    calls = _scripted(monkeypatch, mod, [(1, _A_QUOTA), (0, _reply("unit:0"))])
    brain = ClaudeCodeBrain()

    with pytest.raises(BrainError):
        brain.decide(_world())

    assert len(calls) == 1, "a quota wall must not spend a second attempt"
    assert brain.transient_retries == 0


def test_a_quota_refusal_emits_a_provider_neutral_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import neural_amplifier.claude_code_brain as subject

    event_log = tmp_path / "calls.jsonl"
    monkeypatch.setattr(subject, "PROVIDER_EVENT_LOG", event_log)
    monkeypatch.setattr(subject, "transient_attempts", lambda: 1)
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *args, **kwargs: subject.subprocess.CompletedProcess(
            args[0], 1, '{"result":"You have hit your monthly spend limit"}', ""
        ),
    )

    with pytest.raises(Exception, match="spend limit"):
        ClaudeCodeBrain().decide(_world())

    event = json.loads(event_log.read_text())
    assert event["provider"] == "anthropic"
    assert event["outcome"] == "failure"
    assert event["reason"] == "quota"


def test_telemetry_failure_never_masks_the_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import neural_amplifier.claude_code_brain as subject

    class RefusingPath:
        @property
        def parent(self):
            raise OSError("telemetry unavailable")

    monkeypatch.setattr(subject, "PROVIDER_EVENT_LOG", RefusingPath())
    subject._record_provider_call("failure", "quota")


def test_a_clean_call_does_not_count_a_retry(monkeypatch: Any) -> None:
    """Anti-vacuity: the counter must be able to stay at zero."""
    from neural_amplifier import claude_code_brain as mod

    world = _world()
    action = world.action_space[0].id
    calls = _scripted(monkeypatch, mod, [(0, _reply(action))])
    brain = ClaudeCodeBrain()

    brain.decide(world)

    assert len(calls) == 1
    assert brain.transient_retries == 0


# --------------------------------------------------------------------------------------
# The attempt budget is CONFIGURABLE and named in ATTEMPTS — sattler's cross-arm ruling
# requires both arms to use the same value, which only means something if both read the
# same number the same way. "retries=2" can mean 2 retries (3 attempts) or 2 attempts
# (1 retry), and at ~186s per failing attempt that is 558s vs 372s of blocked game thread.
# --------------------------------------------------------------------------------------

from neural_amplifier.claude_code_brain import (  # noqa: E402
    TRANSIENT_ATTEMPTS_DEFAULT,
    transient_attempts,
)


def test_the_default_is_two_attempts_which_is_one_retry(monkeypatch: Any) -> None:
    monkeypatch.delenv("NA_TRANSIENT_ATTEMPTS", raising=False)
    assert transient_attempts() == 2 == TRANSIENT_ATTEMPTS_DEFAULT


def test_the_env_var_is_honoured(monkeypatch: Any) -> None:
    monkeypatch.setenv("NA_TRANSIENT_ATTEMPTS", "3")
    assert transient_attempts() == 3


def test_it_is_clamped_at_both_ends(monkeypatch: Any) -> None:
    """Below 1 is not a run; above 4 is the regime where retrying makes throughput worse."""
    monkeypatch.setenv("NA_TRANSIENT_ATTEMPTS", "0")
    assert transient_attempts() == 1
    monkeypatch.setenv("NA_TRANSIENT_ATTEMPTS", "-5")
    assert transient_attempts() == 1
    monkeypatch.setenv("NA_TRANSIENT_ATTEMPTS", "99")
    assert transient_attempts() == 4


def test_garbage_falls_back_to_the_default_rather_than_crashing_a_run(monkeypatch: Any) -> None:
    monkeypatch.setenv("NA_TRANSIENT_ATTEMPTS", "two")
    assert transient_attempts() == TRANSIENT_ATTEMPTS_DEFAULT


def test_the_LOOP_honours_the_configured_budget(monkeypatch: Any) -> None:
    """The predicate tests above cannot show that the loop reads the setting."""
    from neural_amplifier import claude_code_brain as mod
    from neural_amplifier.brain import BrainError

    monkeypatch.setenv("NA_TRANSIENT_ATTEMPTS", "3")
    calls = _scripted(monkeypatch, mod, [(1, _A_529), (1, _A_529), (1, _A_529)])
    brain = ClaudeCodeBrain()

    with pytest.raises(BrainError):
        brain.decide(_world())

    assert len(calls) == 3, "the loop ignored NA_TRANSIENT_ATTEMPTS"
    assert brain.transient_retries == 2, "3 attempts is 2 retries"


def test_one_attempt_means_NO_retry(monkeypatch: Any) -> None:
    """Anti-vacuity at the floor: the loop must be able to not retry at all."""
    from neural_amplifier import claude_code_brain as mod
    from neural_amplifier.brain import BrainError

    monkeypatch.setenv("NA_TRANSIENT_ATTEMPTS", "1")
    calls = _scripted(monkeypatch, mod, [(1, _A_529)])
    brain = ClaudeCodeBrain()

    with pytest.raises(BrainError):
        brain.decide(_world())

    assert len(calls) == 1
    assert brain.transient_retries == 0
