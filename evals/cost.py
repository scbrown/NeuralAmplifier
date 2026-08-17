"""What an eval would cost, computed from the prompts it will actually send — na-04v, na-uwp.

## Why this exists

Both remaining eval beads are *spend decisions*: na-04v is "~3.50 USD", na-uwp is "~7 USD". Those
figures live in the bead text, hand-written, and nothing recomputes them. A spend decision made
against a remembered number is the gap this closes — arms get added, prompts grow, and the
estimate in the bead does not move.

This reads the **committed task files**, which is the whole trick: those are the bytes that will
be sent, they are in the repository, and computing over them needs no game data and no network.
So the number is current by construction rather than by somebody remembering to update it.

## What it will not do

**It does not price the model.** Rates change, they differ per model, and a constant compiled in
here would be wrong quietly and confidently — the exact failure this codebase keeps finding. The
price comes in as an argument, defaulting to the *measured* per-call figure recorded in na-04v
($0.088), and the output says where that number came from so nobody quotes it as current truth.

Output tokens are not estimated either. These evals ask for a short structured answer, but "short"
is not a number I can produce honestly without measuring one, so the report gives input tokens
and per-call cost and leaves the rest visible rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

#: Per-call cost measured while running na-qu8's eval, recorded in na-04v's description. NOT a
#: current price and not a rate card — a historical measurement, kept as a default so the
#: command is useful with no arguments and labelled everywhere it appears.
MEASURED_USD_PER_CALL = 0.088

#: Runs per arm, per eval, as each bead actually specifies. NOT one shared default — that is
#: how this tool got its own first answer wrong: 20 is na-04v's figure, na-uwp's bead says
#: "4 decisions x 2 arms x 10 runs = 80 calls ~ $7", and pricing na-htm at 20 silently reported
#: $14.08 for a $7 decision.
#:
#: A doubled estimate is the safe direction and still the wrong number: someone deciding whether
#: to spend deserves the figure their own bead committed to, not a default that happens to be
#: conservative.
RUNS_PER_ARM = {
    "na-qu8": 20,  # na-04v: 2 arms x 20 runs = 40 calls
    "na-htm": 10,  # na-uwp: 8 arms x 10 runs = 80 calls
}

#: Used when an eval has no entry above. Stated rather than silently applied.
DEFAULT_RUNS_PER_ARM = 20

#: Characters per token, for input sizing only. A rule of thumb, stated as one: the point of the
#: report is the CALL COUNT and the relative size of the arms, both of which are exact.
CHARS_PER_TOKEN = 4


def arms_on_disk(run_dir: Path) -> dict[str, int]:
    """{arm name: prompt bytes} from the committed task files.

    The files are the deliverable of `just eval prompts`, so if they are stale the cost is stale
    in exactly the same way the run would be — which is the honest coupling.
    """
    return {
        path.name[: -len(".task.txt")]: path.stat().st_size
        for path in sorted(run_dir.glob("*.task.txt"))
    }


def estimate(run_dir: Path, runs_per_arm: int, usd_per_call: float) -> dict:
    arms = arms_on_disk(run_dir)
    calls = len(arms) * runs_per_arm
    prompt_bytes = sum(arms.values()) * runs_per_arm
    return {
        "arms": arms,
        "runs_per_arm": runs_per_arm,
        "calls": calls,
        "prompt_bytes": prompt_bytes,
        "approx_input_tokens": prompt_bytes // CHARS_PER_TOKEN,
        "usd_per_call": usd_per_call,
        "usd_total": round(calls * usd_per_call, 2),
    }


def runs_for(eval_id: str, override: int | None) -> tuple[int, str]:
    """(runs per arm, where the number came from). The provenance is half the answer."""
    if override is not None:
        return override, "given on the command line"
    if eval_id in RUNS_PER_ARM:
        return RUNS_PER_ARM[eval_id], f"{eval_id}'s own bead"
    return DEFAULT_RUNS_PER_ARM, "the default — this eval's bead does not specify one"


def report(
    eval_id: str, run_dir: Path, runs_per_arm: int, usd_per_call: float, source: str = "given"
) -> int:
    if not run_dir.is_dir():
        print(f"no committed run at {run_dir} — run `just eval prompts {eval_id}` first")
        return 1

    result = estimate(run_dir, runs_per_arm, usd_per_call)
    if not result["arms"]:
        print(f"no *.task.txt in {run_dir} — nothing to price")
        return 1

    print(f"{eval_id}: {len(result['arms'])} arm(s) x {runs_per_arm} runs ({source})")
    for arm, size in result["arms"].items():
        print(f"  {arm:<42} {size:>7} bytes")
    print()
    print(f"  calls                 {result['calls']}")
    tokens = result["approx_input_tokens"]
    print(f"  approx input tokens   {tokens:,}  (at ~{CHARS_PER_TOKEN} chars/token)")
    print(f"  USD per call          {result['usd_per_call']}")
    print(f"  ESTIMATED TOTAL       ${result['usd_total']}")
    print()
    print(
        "The per-call figure is a MEASUREMENT from na-04v's run, not a current rate. Output\n"
        "tokens are not included — these evals ask for a short structured answer, and guessing\n"
        "its length would put an invented number next to two exact ones.\n"
        "\n"
        "The call count and the arm sizes ARE exact: they come from the committed task files,\n"
        "which are the bytes that would be sent."
    )
    return 0
