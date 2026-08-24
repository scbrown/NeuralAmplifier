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
    """"No output" is a finding. It must not render as a trailing colon and nothing else."""
    why = _why()(_done("", ""))
    assert why == "both stdout and stderr were EMPTY"
    assert not why.endswith(":")


def test_the_message_is_bounded() -> None:
    """A brain error becomes a degrade_reason on every fallback record; it cannot be unbounded."""
    why = _why()(_done(json.dumps({"result": "x" * 5000}), "y" * 5000))
    assert len(why) < 700
