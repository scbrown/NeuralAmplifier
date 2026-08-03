"""The eval entry point — ``just eval <cmd>``. See ``evals/README.md``.

Three commands, matching the three steps an eval has:

``list``            what evals exist, what each asked, and what it found.
``prompts <id>``    regenerate the task files. Needs the Thinker rulebook.
``score <id>``      score whatever answered them. Needs only the committed run.
``check [<id>]``    do the committed answers still belong to the current prompt?

``check`` exists because the alternative is a stale eval being believed. A prompt changes, the
committed answers quietly become evidence about something we no longer ship, and ``score`` keeps
printing the same confident table. It has already happened twice here.

Two more commands exist for evals that define them, and both come from na-htm's post-mortem of
na-373 — a published statistic that was never checked against a case with a known answer, over
arms that had drifted from what the retriever did:

``selftest <id>``   does the scorer recover results that are arithmetic rather than measured?
``harvest <id>``    has the eval's pinned input drifted from what the live retriever now serves?

The split is the point. Scoring a committed run works on a fresh checkout with no game, no
sibling repo and no model, so a finding can be re-derived by anyone who doubts it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_history  # noqa: E402
import grounding_compaction  # noqa: E402
import hurry_grounding  # noqa: E402
import multi_decision_ranking  # noqa: E402
import retrieval_ranking  # noqa: E402
from harness import DEFAULT_LINKS, RUNS, check_prompts, write_prompts  # noqa: E402

#: id → (module, one-line question, the answer we got). The answer belongs here because an eval
#: whose result you have to go and find is one nobody rereads before repeating it.
EVALS = {
    "na-373": (
        retrieval_ranking,
        "Can we offer fewer grounding facts by ranking them?",
        "WITHDRAWN — the refutation was fixture-bound; one near-unanimous world view cannot\n"
        " measure a ranking rule at all. Ranking stays out: unmeasured, not refuted (na-htm).",
    ),
    "na-61c2": (
        build_history,
        "Does recent build history stop a base flip-flopping?",
        "Yes — 0.10 to 1.00 continued, and 0.00 when the case actually changed.",
    ),
    "na-vbe": (
        grounding_compaction,
        "Can grounding drop what the action space already carries, without moving the choice?",
        "Yes — grounding 43% smaller and the choice held 20/20 vs 19/20 (Fisher p=1.0).",
    ),
    "na-qu8": (
        hurry_grounding,
        "Does grounding base.hurry's subject move its stability, and toward what?",
        "UNANSWERED — built and self-tested, not run. The capture was recovered and the\n"
        " treatment verified (the subject grounds, both action labels do not). Stability is\n"
        " never reported alone here: this surface spends credits, so 'more stable' is not\n"
        " 'better' unless tier agreement holds too.",
    ),
    "na-htm": (
        multi_decision_ranking,
        "Does ranking predict citation, measured across decisions instead of within one?",
        "UNANSWERED — built and self-tested, not yet run. na-373's question, re-posed over four\n"
        " decisions whose fact pools are near-disjoint, so no single fact can carry the number.\n"
        " Answering it is a paid run; the design, the dominance gate and the null are in place.",
    ),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["list", "prompts", "score", "check", "selftest", "harvest"])
    ap.add_argument("eval_id", nargs="?", help=f"one of: {', '.join(EVALS)}")
    ap.add_argument("--links", type=Path, default=DEFAULT_LINKS)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.cmd == "list":
        for name, (_, question, answer) in EVALS.items():
            print(f"{name}\n  asks:  {question}\n  found: {answer}")
        return

    if args.cmd == "check":
        ids = [args.eval_id] if args.eval_id else list(EVALS)
        stale = 0
        for name in ids:
            module, _, _ = EVALS[name]
            stale += check_prompts(RUNS / name, module.arms(args.links), name)
        raise SystemExit(1 if stale else 0)

    if args.eval_id not in EVALS:
        ap.error(f"unknown eval {args.eval_id!r}; try one of: {', '.join(EVALS)}")
    module, question, _ = EVALS[args.eval_id]
    out = args.out or RUNS / args.eval_id

    # Both are optional per eval, and a missing one says so rather than failing: an eval with no
    # pinned input has nothing to harvest, and saying "not defined" is the honest answer.
    if args.cmd in {"selftest", "harvest"}:
        fn = getattr(module, args.cmd, None)
        if fn is None:
            print(f"{args.eval_id}: no {args.cmd} defined")
            return
        raise SystemExit(fn())

    if args.cmd == "prompts":
        # Not every eval reads the rulebook. na-htm's arms are grounded through Quipu and pinned
        # to a committed file, so demanding `alphax.txt` would refuse a command that needs
        # nothing — and the refusal reads as "this eval is unavailable here" rather than as a
        # guard misfiring.
        if getattr(module, "NEEDS_LINKS", True) and not args.links.exists():
            ap.error(
                f"{args.links} not found. Regenerating prompts needs the Thinker fork's"
                " rulebook; set --links or THINKER_DIR. Scoring the committed run does not."
            )
        print(f"{args.eval_id}: {question}")
        write_prompts(out, module.arms(args.links), note=question)
        print("\nAnswer each <arm>.task.txt and collect replies into <arm>.answers.jsonl,")
        print(f"one JSON object per line, then: just eval score {args.eval_id}")
        return

    if not out.exists():
        ap.error(f"no run at {out}")
    print(f"{args.eval_id}: {question}\n")
    module.score(out, args.links)


if __name__ == "__main__":
    main()
