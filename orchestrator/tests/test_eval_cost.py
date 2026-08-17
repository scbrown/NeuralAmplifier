"""What an eval would cost, from the prompts it will actually send — na-04v, na-uwp.

Both remaining eval beads are spend decisions, and their figures ("~3.50 USD", "~7 USD") live in
the bead text, hand-written, with nothing recomputing them. These tests pin the two properties
that make the computed version trustworthy: it reads the committed task files (so it is current
by construction), and it uses each eval's OWN run count rather than one shared default.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("eval_cost", REPO / "evals" / "cost.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cost = _module()


def test_the_run_count_comes_from_each_evals_own_bead() -> None:
    """One shared default gave this tool its own first wrong answer.

    na-04v specifies 2 arms x 20 runs; na-uwp specifies 8 arms x 10. Pricing na-htm at 20
    reported $14.08 for a decision its bead had costed at $7 — double, which is the safe
    direction and still the wrong number. Somebody deciding whether to spend deserves the figure
    their own bead committed to, not a default that happens to be conservative.
    """
    assert cost.runs_for("na-qu8", None) == (20, "na-qu8's own bead")
    assert cost.runs_for("na-htm", None) == (10, "na-htm's own bead")

    runs, source = cost.runs_for("na-unknown", None)
    assert runs == cost.DEFAULT_RUNS_PER_ARM
    assert "does not specify" in source, "an assumed number must say that it was assumed"

    assert cost.runs_for("na-qu8", 5) == (5, "given on the command line")


def test_the_committed_estimates_match_the_beads() -> None:
    """The check that would catch the arms drifting from the costed figure.

    na-04v: 2 arms x 20 runs = 40 calls ~ $3.50. na-uwp: 8 arms x 10 = 80 calls ~ $7.
    If someone adds an arm these fail — which is the point, since the bead's number would
    otherwise silently stop being true.
    """
    qu8 = cost.estimate(REPO / "evals" / "runs" / "na-qu8", 20, cost.MEASURED_USD_PER_CALL)
    assert qu8["calls"] == 40, "na-04v's bead says 2 arms x 20 runs"
    assert 3.4 <= qu8["usd_total"] <= 3.6, "na-04v costed this at ~$3.50"

    htm = cost.estimate(REPO / "evals" / "runs" / "na-htm", 10, cost.MEASURED_USD_PER_CALL)
    assert htm["calls"] == 80, "na-uwp's bead says 4 decisions x 2 arms x 10 runs"
    assert 6.9 <= htm["usd_total"] <= 7.2, "na-uwp costed this at ~$7"


def test_it_prices_the_bytes_that_would_actually_be_sent() -> None:
    """Reading the committed task files is what keeps the number current.

    Estimating from a remembered arm count is how the bead figures went stale in the first
    place. These are the files `just eval prompts` writes and the run consumes.
    """
    arms = cost.arms_on_disk(REPO / "evals" / "runs" / "na-qu8")
    assert set(arms) == {"bare", "subject"}, "na-04v's two arms"
    assert all(size > 0 for size in arms.values())


def test_an_empty_or_missing_run_is_reported_not_priced(tmp_path, capsys) -> None:
    """Pricing a directory with no prompts would report $0.00 for a spend that has not been
    prepared — a confident number about nothing."""
    assert cost.report("na-x", tmp_path / "absent", 10, 0.088) == 1
    (tmp_path / "empty").mkdir()
    assert cost.report("na-x", tmp_path / "empty", 10, 0.088) == 1
    assert "nothing to price" in capsys.readouterr().out
