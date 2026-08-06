"""na-373 — does ranking grounding by information value let us offer fewer facts?

**Withdrawn, and superseded by na-htm.** This eval reported that ranking predicts citation
worse than chance; re-measurement withdrew it, and the reversal was no better. Both numbers
reduce to one fact's rank — 19 of the 27 baseline citations here are ``fac:network-node``, so
"citations in the top k" is really "did network-node make the cut". The choice half has the
same defect from the other side: this world view sits 19/20 for one option, so an arm can only
move it down.

The rule still lives here rather than in the retriever, but the reason has changed and the
distinction matters. It is **unmeasured**, not refuted. ``multi_decision_ranking`` re-poses the
question over four decisions whose fact pools are near-disjoint, which is what one world view
could never do; see ``runs/na-htm/NOTES.md`` and ``docs/quipu-integration.md``.

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
    """What a fact adds beyond the option's own name — the rule under test, still untested.

    Deliberately blind to cost and affordability. Ranking by desirability would ground the
    attractive options better than the rest, biasing the choice through retrieval instead of
    informing it, and invisibly: the record shows which facts were offered, never which options
    were left comparatively unargued.

    This used to say the rule fails anyway, and gave the mechanism: grounding is roughly one
    fact per option, so dropping a fact removes the argument for a *particular option* rather
    than removing information, and an unexplained option loses. The **mechanism** is still the
    best account of how truncation could hurt, and it is why ``multi_decision_ranking`` scores
    choice agreement between the arms. What it is not is a measured result — the run that
    "showed" it was one near-unanimous world view. Held as a hypothesis with an experiment
    attached, not as a finding.
    """
    known = set(_words(label))
    fresh = [w for w in _words(fact) if w not in known]
    return len(fresh) + sum(1.0 for w in fresh if any(c.isdigit() for c in w))


#: Separators that end an option's name. Order here is irrelevant — the EARLIEST match in the
#: line wins, never the first one listed (na-5to).
_NAME_ENDS = (" — ", "; ")


def label_of(line: str) -> str:
    """The option's own name: the text between the fact id and the first separator.

    Module-level, and deliberately so. This lived inside :func:`rank_lines` as a closure, which
    made the rule's own output distribution unreachable from outside — so the only way to check
    it was to re-type the derivation in the checking script, and a checking script holding its
    own copy of the thing under test reports on the copy. That is exactly how na-5to survived:
    the scoring looked verifiable and was not.

    Two fact formats reach this and the name ends at whichever separator comes FIRST. na-373's
    VERBOSE fixture separates with " — "; every fact a real retriever emits separates with "; "
    — both ``QuipuRetriever.format_row`` and ``DatalinksRetriever.describe(compact=True)``.
    Splitting on the em dash alone returned the WHOLE fact as the name on real grounding, so
    ``known`` swallowed every content word and the only "fresh" words left were the id tokens.
    Splitting on "; " first would be wrong the other way: it yields "Recycling Tanks — cost 4"
    on the verbose format and quietly changes na-373's ranking.
    """
    if " " not in line:
        return ""
    rest = line.split(" ", 1)[1]
    cuts = [i for i in (rest.find(sep) for sep in _NAME_ENDS) if i >= 0]
    return rest[: min(cuts)] if cuts else rest


def rank_lines(lines: list[str]) -> list[str]:
    """Order grounding lines by information value, most informative first.

    Takes the lines rather than a world view so scoring a committed run needs nothing but the
    committed run — :func:`label_of` is all the rule reads. Re-deriving this from the rulebook
    would make every published number depend on a sibling checkout being present and unchanged.

    ``sorted`` is stable, so lines that tie keep retriever order. That is the right tie-break,
    but it also means a rule that scores everything the same is INVISIBLE here: the arm still
    returns a plausible ranking, just the input one. Check the score distribution, not the
    output shape — see :func:`score_distribution`.
    """
    return sorted(lines, key=lambda ln: -information_value(ln, label_of(ln)))


def score_distribution(lines: list[str]) -> dict[float, int]:
    """How many facts landed on each score — the rule's discrimination, as a number.

    A ranking rule that returns two distinct values over 40 facts has not ranked them, and
    nothing downstream can tell: the arm looks ordered because ``sorted`` is stable. This is the
    check na-5to's fix has to pass, so it lives beside the rule rather than in a scratch script.
    """
    counts: dict[float, int] = {}
    for ln in lines:
        s = information_value(ln, label_of(ln))
        counts[s] = counts.get(s, 0) + 1
    return counts


def _ranked(view: Any, retriever: Any, keep: int | None) -> Any:
    full = ground(view, retriever)
    order = rank_lines(list(full.grounding or []))
    return full.model_copy(update={"grounding": order[:keep] if keep else order})


def arms(links: Path, keep: int = KEEP) -> dict[str, Any]:
    view, retriever = base_production(links)
    return {"all": ground(view, retriever), "ranked": _ranked(view, retriever, keep)}


#: The `all` arm as it was measured: full facts, carrying cost and prerequisite. Kept verbatim
#: so the compression arm has something to be compared against — regenerating it from the
#: retriever would move both sides at once, which is how na-61c2's arms drifted.
VERBOSE = (
    "fac:recycling-tanks Recycling Tanks — cost 4; Bonus Resources; needs Biogenetics",
    "fac:recreation-commons Recreation Commons — cost 4; upkeep 1/turn; Fewer Drones;"
    " needs Social Psych",
    "fac:energy-bank Energy Bank — cost 8; upkeep 1/turn; Economy Bonus;"
    " needs Industrial Economics",
    "fac:network-node Network Node — cost 8; upkeep 1/turn; Labs Bonus; needs Information Networks",
    "fac:perimeter-defense Perimeter Defense — cost 5; Defense +100%; needs Doctrine: Loyalty",
    "fac:command-center Command Center — cost 4; upkeep 1/turn; +2 Morale:Land;"
    " needs Doctrine: Mobility",
    "fac:research-hospital Research Hospital — cost 12; upkeep 3/turn; Labs and Psych Bonus;"
    " needs Gene Splicing",
    "fac:children-s-creche Children's Creche — cost 5; upkeep 1/turn; Growth/Effic/Morale;"
    " needs Ethical Calculus",
)


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
        used = [len({c for c in dict.fromkeys(r.get("cited", [])) if c in offered}) for r in rows]
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
