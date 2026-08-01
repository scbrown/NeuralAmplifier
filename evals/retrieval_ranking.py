"""na-373 — does ranking grounding by information value let us offer fewer facts?

**Answered: no.** Ranking then truncating raises utilisation and changes the decision, and the
ranking rule predicts citation worse than chance. The rule lives here rather than in the
retriever because of that result; see `docs/quipu-integration.md`.

Arms:

``all``     every fact the action space pulls, in action-space order. What ships.
``ranked``  the same facts scored by :func:`information_value` and cut to ``--keep``.

Two scores, and the second is the one that matters. ``score`` reports utilisation and the choice
distribution — utilisation alone rewards offering less, so it is never read without the choices
beside it. ``predict`` asks where the *baseline's* citations sat in the ranking, which tests the
rule directly and cannot be gamed by truncation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness import base_production, ground, load_answers, spread, tally  # noqa: F401

#: How many facts the truncating arm keeps.
KEEP = 4

#: Words too common to count as information.
_NOISE = frozenset({"a", "an", "the", "and", "or", "of", "to", "per", "for", "with", "upkeep"})
_WORD = re.compile(r"[a-z0-9+%-]+")


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _NOISE]


def information_value(fact: str, label: str) -> float:
    """What a fact adds beyond the option's own name — the rule under test, and refuted.

    Deliberately blind to cost and affordability. Ranking by desirability would ground the
    attractive options better than the rest, biasing the choice through retrieval instead of
    informing it, and invisibly: the record shows which facts were offered, never which options
    were left comparatively unargued.

    It fails anyway, for a reason that outlives the rule. Grounding is one fact per option, so
    dropping a fact removes the argument for a *particular option* rather than removing
    information — and an unexplained option loses.
    """
    known = set(_words(label))
    fresh = [w for w in _words(fact) if w not in known]
    return len(fresh) + sum(1.0 for w in fresh if any(c.isdigit() for c in w))


def rank_lines(lines: list[str]) -> list[str]:
    """Order grounding lines by information value, most informative first.

    Takes the lines rather than a world view so scoring a committed run needs nothing but the
    committed run — the option's name is the text between the id and the em dash, which is all
    the rule reads. Re-deriving this from the rulebook would make every published number
    depend on a sibling checkout being present and unchanged.
    """

    def label_of(line: str) -> str:
        return line.split(" ", 1)[1].split(" — ")[0] if " " in line else ""

    return sorted(lines, key=lambda ln: -information_value(ln, label_of(ln)))


def _ranked(view: Any, retriever: Any, keep: int | None) -> Any:
    full = ground(view, retriever)
    order = rank_lines(list(full.grounding or []))
    return full.model_copy(update={"grounding": order[:keep] if keep else order})


def arms(links: Path, keep: int = KEEP) -> dict[str, Any]:
    view, retriever = base_production(links)
    return {"all": ground(view, retriever), "ranked": _ranked(view, retriever, keep)}


def score(out: Path, links: Path, keep: int = KEEP) -> None:
    print(f"{'arm':10} {'n':>3} {'offered':>8} {'cited':>6} {'util':>6}  choices")
    for name in ("all", "ranked"):
        rows = load_answers(out, name)
        if not rows:
            continue
        import json

        offered = set(json.loads((out / f"{name}.offered.json").read_text()))
        # Filtered against the offered set exactly as `summarise` does: an invented id must not
        # inflate the number this whole exercise exists to move.
        used = [
            len({c for c in dict.fromkeys(r.get("cited", [])) if c in offered}) for r in rows
        ]
        util = sum(u / len(offered) for u in used) / len(rows) if offered else 0.0
        print(
            f"{name:10} {len(rows):>3} {len(offered):>8} {sum(used) / len(rows):>6.2f} "
            f"{util:>6.2f}  {spread(tally(rows))}"
        )

    # The half that truncation cannot flatter. Read back off the committed baseline prompt, so
    # this needs no rulebook and no model — only the run.
    baseline = (out / "all.task.txt").read_text()
    block = re.search(r'"grounding": \[(.*?)\n  \]', baseline, re.S)
    lines = re.findall(r'"((?:[a-z]+:[a-z0-9-]+) [^"]*)"', block.group(1)) if block else []
    ranking = [ln.split(" ", 1)[0] for ln in rank_lines(lines)]
    position = {fid: i for i, fid in enumerate(ranking)}
    hits = [
        position[c]
        for r in load_answers(out, "all")
        for c in dict.fromkeys(r.get("cited", []))
        if c in position
    ]
    if not hits:
        return
    top = sum(1 for h in hits if h < keep)
    print(f"\nwhere the baseline's citations sat in the ranking ({len(hits)} citations):")
    print(f"  in the top {keep}: {top}/{len(hits)} = {top / len(hits):.2f}")
    print(f"  if the ranking were noise: {keep / len(ranking):.2f}")
    for fid in sorted({ranking[h] for h in hits}, key=lambda f: position[f]):
        print(f"  rank {position[fid]}  {fid}  ×{sum(1 for h in hits if h == position[fid])}")
