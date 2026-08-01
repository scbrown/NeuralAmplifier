"""The eval entry point — ``just eval <cmd>``. See ``evals/README.md``.

Three commands, matching the three steps an eval has:

``list``            what evals exist, what each asked, and what it found.
``prompts <id>``    regenerate the task files. Needs the Thinker rulebook.
``score <id>``      score whatever answered them. Needs only the committed run.

The split is the point. Scoring a committed run works on a fresh checkout with no game, no
sibling repo and no model, so a finding can be re-derived by anyone who doubts it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_history  # noqa: E402
import retrieval_ranking  # noqa: E402
from harness import DEFAULT_LINKS, RUNS, write_prompts  # noqa: E402

#: id → (module, one-line question, the answer we got). The answer belongs here because an eval
#: whose result you have to go and find is one nobody rereads before repeating it.
EVALS = {
    "na-373": (
        retrieval_ranking,
        "Can we offer fewer grounding facts by ranking them?",
        "No — truncation changed the decision, and the rule predicts citation worse than chance.",
    ),
    "na-61c2": (
        build_history,
        "Does recent build history stop a base flip-flopping?",
        "Yes — 0.30 to 1.00 continued, and still 0.10 when the case actually changed.",
    ),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["list", "prompts", "score"])
    ap.add_argument("eval_id", nargs="?", help=f"one of: {', '.join(EVALS)}")
    ap.add_argument("--links", type=Path, default=DEFAULT_LINKS)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.cmd == "list":
        for name, (_, question, answer) in EVALS.items():
            print(f"{name}\n  asks:  {question}\n  found: {answer}")
        return

    if args.eval_id not in EVALS:
        ap.error(f"unknown eval {args.eval_id!r}; try one of: {', '.join(EVALS)}")
    module, question, _ = EVALS[args.eval_id]
    out = args.out or RUNS / args.eval_id

    if args.cmd == "prompts":
        if not args.links.exists():
            ap.error(
                f"{args.links} not found. Regenerating prompts needs the Thinker fork's"
                " rulebook; set --links or THINKER_DIR. Scoring the committed run does not."
            )
        print(f"{args.eval_id}: {question}")
        write_prompts(out, module.arms(args.links), note=question)
        print(
            "\nAnswer each <arm>.task.txt and collect replies into <arm>.answers.jsonl,"
        )
        print(f"one JSON object per line, then: just eval score {args.eval_id}")
        return

    if not out.exists():
        ap.error(f"no run at {out}")
    print(f"{args.eval_id}: {question}\n")
    module.score(out, args.links)


if __name__ == "__main__":
    main()
