"""A recording that is not there must be an ERROR, never a clean empty result (na-x5i).

Every log-reading command here consumes a recording made by an earlier run, and until this
landed a path that simply did not exist produced a well-formed zero. Two of the three ways that
showed up were live on the DEFAULT invocation, because the justfile's defaults pointed at the
repo root while `just play serve` writes the log inside `orchestrator/`:

    just coverage   ->  decisions: 0, fair_play: true, fog_enforced: true, EXIT 0
    just replay     ->  decisions: 0, "FAIL: nothing was replayed — the store has none of the
                        log's inputs"

The first is the worse one and it is not the one that got filed. `coverage` is described in the
justfile as failing "if the brain was largely absent or an illegal action slipped through", and
it passed **green** on a file that was not there. A gate that reports all-clear over a missing
input is not a weaker gate; it is a false negative wearing a gate's clothes, and nothing
downstream can tell.

The second is what na-x5i reports, and its sentence sends you the wrong way: it names the STORE
when the truth is that the LOG was not found, so an investigation starts at the recording that
was fine. Same for the third path — a typo'd `--store` — where `WorldViewStore` mkdirs its own
root, so replay MANUFACTURES the empty store it then blames.

So every assertion below is on the *words*, not merely on the exit code. "Something failed" was
never the missing information.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_amplifier.cli import main


def a_log(tmp_path: Path) -> Path:
    """A syntactically valid but empty decision log — a run that recorded nothing.

    Deliberately distinct from a missing file: the two want opposite next moves, and collapsing
    them is the defect this file is about.
    """
    path = tmp_path / "d.jsonl"
    path.write_text("")
    return path


@pytest.mark.parametrize("command", ["coverage", "learn"])
def test_a_missing_log_is_refused_by_name(command: str, tmp_path: Path, capsys) -> None:
    gone = tmp_path / "never-recorded.jsonl"
    assert main([command, str(gone)]) == 2
    err = capsys.readouterr().err
    assert "does not exist" in err
    assert str(gone) in err, "the path is the whole diagnosis; a generic failure is not enough"


def test_coverage_no_longer_reports_a_clean_run_over_a_file_that_is_not_there(
    tmp_path: Path, capsys
) -> None:
    """The regression that matters most, asserted on what it used to print.

    `fair_play: true` and `fog_enforced: true` over a nonexistent log is a green CI job telling
    you the game was fair. Nothing about that output looks wrong.
    """
    assert main(["coverage", str(tmp_path / "gone.jsonl")]) == 2
    captured = capsys.readouterr()
    assert "fair_play" not in captured.out, "no verdict may be printed about a file not read"


def test_a_missing_log_is_refused_by_replay_and_the_store_is_not_blamed(
    tmp_path: Path, capsys
) -> None:
    store = tmp_path / "views"
    store.mkdir()
    assert main(["replay", str(tmp_path / "gone.jsonl"), "--store", str(store)]) == 2
    err = capsys.readouterr().err
    assert "decision log does not exist" in err
    assert "the store has none of the log's inputs" not in err, (
        "the old message pointed the investigation at a store that was fine"
    )


def test_a_missing_store_is_refused_and_NOT_created(tmp_path: Path, capsys) -> None:
    """The check has to run before `WorldViewStore`, which mkdirs its own root.

    That constructor is right on the recording path — the run owns the directory — and quietly
    destructive here: it makes a typo'd `--store` produce the empty store replay then reports as
    having none of the log's inputs, which is a true sentence about a directory replay had just
    created itself.
    """
    typo = tmp_path / "wroldviews"
    assert main(["replay", str(a_log(tmp_path)), "--store", str(typo)]) == 2
    err = capsys.readouterr().err
    assert "world-view store does not exist" in err
    assert not typo.exists(), "replay must not create the store it is about to blame"


def test_a_file_where_a_directory_belongs_says_so(tmp_path: Path, capsys) -> None:
    not_a_dir = tmp_path / "views"
    not_a_dir.write_text("{}")
    assert main(["replay", str(a_log(tmp_path)), "--store", str(not_a_dir)]) == 2
    assert "is not a directory" in capsys.readouterr().err


def test_an_empty_log_is_reported_as_empty_rather_than_as_a_store_problem(
    tmp_path: Path, capsys
) -> None:
    """Present-and-empty is a different fault from absent, and from a store mismatch.

    An empty log means the RECORDING did not happen — `NA_DECISION_LOG` unset, or a run that
    never reached a decision. Sending that reader to look at the store is a wasted search, and
    it is the search the single old message sent everyone on.
    """
    store = tmp_path / "views"
    store.mkdir()
    assert main(["replay", str(a_log(tmp_path)), "--store", str(store)]) == 1
    err = capsys.readouterr().err
    assert "the decision log is empty" in err
    assert "the store has none" not in err


def test_a_real_log_whose_inputs_are_genuinely_absent_still_says_so(
    tmp_path: Path, capsys, thinker_base
) -> None:
    """The positive control for the message above.

    Every test here so far asserts an error appears. That is exactly the shape that passes when
    a command has been broken into refusing everything — so one case must still reach the
    original diagnosis, with a log that has records and a store that really does lack them.
    """
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.decisions import DecisionLog
    from neural_amplifier.orchestrator import Orchestrator

    log_path = tmp_path / "d.jsonl"
    Orchestrator(ScriptedBrain(), log=DecisionLog(log_path)).decide(thinker_base)
    empty_store = tmp_path / "views"
    empty_store.mkdir()

    assert main(["replay", str(log_path), "--store", str(empty_store)]) == 1
    captured = capsys.readouterr()
    assert "the store has none of the log's inputs" in captured.err
    assert json.loads(captured.out)["decisions"] == 1
