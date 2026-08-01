"""Measure grounding utilisation with and without information-value ranking (na-373).

Builds one real `base.production` world view, grounds it through the real retriever, and emits
the exact prompt the brain would send — twice, once per configuration:

``all``     every fact the action space pulls, in action-space order. What shipped before.
``ranked``  the same facts ranked by information value and truncated to ``--keep``.

It does not call a model. It writes prompts, and reads back whatever answered them, so the
answering can come from anywhere — a paid API run, or agents driving the same prompt. What is
measured is identical either way: utilisation = cited ÷ offered, plus the choice, so a rise in
utilisation bought by dropping a fact the decision needed is visible rather than scored as a win.

    python3 scripts/retrieval_utilisation.py prompts --out runs/
    python3 scripts/retrieval_utilisation.py score  --out runs/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from neural_amplifier.brain import _SYSTEM  # noqa: E402
from neural_amplifier.contract import Action, WorldView  # noqa: E402
from neural_amplifier.datalinks.briefing import DatalinksRetriever  # noqa: E402
from neural_amplifier.datalinks.parse import parse  # noqa: E402

#: Words too common to count as information.
_NOISE = frozenset({"a", "an", "the", "and", "or", "of", "to", "per", "for", "with", "upkeep"})
_WORD = re.compile(r"[a-z0-9+%-]+")


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _NOISE]


def information_value(fact: str, label: str) -> float:
    """The ranking rule under test — and, on the evidence below, refuted.

    Scores a fact by what it adds beyond the option's own name: words not already in the label,
    with a bonus for numbers, on the theory that "an option whose name implies its effect needs
    no fact". Deliberately blind to cost and affordability, since ranking by desirability would
    bias the choice through retrieval rather than inform it.

    It lives in the harness rather than in the retriever because measurement said not to ship
    it: it predicts citation *worse than chance*, and truncating to its top 4 changed the
    decision. Kept so the experiment can be re-run against a better rule.
    """
    known = set(_words(label))
    fresh = [w for w in _words(fact) if w not in known]
    return len(fresh) + sum(1.0 for w in fresh if any(c.isdigit() for c in w))

#: The eight options the measured decision offered. Real facility names, so they resolve in the
#: datalinks graph — a made-up option would ground to nothing and measure the fixture instead of
#: the retriever.
OPTIONS = [
    "Recycling Tanks",
    "Recreation Commons",
    "Energy Bank",
    "Network Node",
    "Perimeter Defense",
    "Command Center",
    "Research Hospital",
    "Children's Creche",
]


def world_view(links_path: Path) -> tuple[WorldView, DatalinksRetriever]:
    links = parse(links_path.read_text(errors="replace"))
    view = WorldView(
        engine="thinker",
        scope="base",
        surface_id="base.production",
        turn=35,
        faction="University",
        subjects=["University Base"],
        action_space=[
            Action(id=f"build:{i}", action=name) for i, name in enumerate(OPTIONS)
        ],
        metrics={
            "energy_reserves": 82,
            "energy_income": 14,
            "labs_output": 21,
            "base_count": 4,
            "pop_total": 13,
            "military_units": 3,
            "drone_total": 2,
            "pop_size": 4,
            "mineral_surplus": 3,
            "minerals_remaining": 26,
        },
    )
    return view, DatalinksRetriever(links, engine="thinker")


def ground(view: WorldView, retriever: DatalinksRetriever, keep: int | None) -> WorldView:
    """Inject grounding exactly as ``orchestrator.decide`` does — id-first lines.

    ``keep`` applies the ranking rule and truncates. ``None`` leaves the retriever's own order,
    which is the action-space order the orchestrator actually ships.
    """
    g = retriever.retrieve(view)
    ids, facts = list(g.fact_ids), list(g.facts)
    if keep is not None:
        labels = [a.action for a in view.action_space]
        by_name = {i: labels[n] if n < len(labels) else "" for n, i in enumerate(ids)}
        order = sorted(
            range(len(facts)),
            key=lambda n: -information_value(facts[n], by_name.get(ids[n], "")),
        )[:keep]
        ids, facts = [ids[n] for n in order], [facts[n] for n in order]
    lines = [f"{i} {t}" for i, t in zip(ids, facts, strict=True)]
    return view.model_copy(update={"grounding": lines})


def cmd_prompts(args: argparse.Namespace) -> None:
    view, retriever = world_view(args.links)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    configs = {"all": ground(view, retriever, None), "ranked": ground(view, retriever, args.keep)}
    for name, wv in configs.items():
        (out / f"{name}.system.txt").write_text(_SYSTEM)
        (out / f"{name}.worldview.json").write_text(wv.model_dump_json(indent=2))
        offered = [line.split(" ", 1)[0] for line in wv.grounding or []]
        (out / f"{name}.offered.json").write_text(json.dumps(offered, indent=2))
        print(f"{name}: {len(offered)} facts offered -> {out}/{name}.*")


def cmd_score(args: argparse.Namespace) -> None:
    """Read `<config>.answers.jsonl` ({"choice": id, "cited": [ids]} per line) and score."""
    print(f"{'config':10} {'n':>3} {'offered':>8} {'cited':>6} {'util':>6}  choices")
    for name in ("all", "ranked"):
        answers_path = args.out / f"{name}.answers.jsonl"
        if not answers_path.exists():
            continue
        offered = set(json.loads((args.out / f"{name}.offered.json").read_text()))
        rows = [json.loads(x) for x in answers_path.read_text().splitlines() if x.strip()]
        if not rows:
            continue
        # Filtered against the offered set, exactly as `summarise` does — an invented id must
        # not inflate the number this whole exercise exists to move.
        utils, choices = [], []
        for r in rows:
            cited = {c for c in dict.fromkeys(r.get("cited", [])) if c in offered}
            utils.append(len(cited) / len(offered) if offered else 0.0)
            choices.append(r.get("choice", "?"))
        counts: dict[str, int] = {}
        for c in choices:
            counts[c] = counts.get(c, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])
        spread = " ".join(f"{k}×{v}" for k, v in top)
        mean_cited = sum(u * len(offered) for u in utils) / len(utils)
        print(
            f"{name:10} {len(rows):>3} {len(offered):>8} {mean_cited:>6.2f} "
            f"{sum(utils) / len(utils):>6.2f}  {spread}"
        )


def cmd_predict(args: argparse.Namespace) -> None:
    """Does the ranking rule predict which facts get cited?

    The sharpest question available, and the one that does not reward truncation for its own
    sake. Utilisation is cited ÷ offered, so dropping facts raises it mechanically whether or
    not the right ones were dropped. This instead asks: on the *baseline* run, where all eight
    facts were offered, where did the cited ones sit in the ranking? A rule that works puts
    them near the top. A rule that is noise scatters them uniformly.
    """
    view, retriever = world_view(args.links)
    ranked_ids = [
        line.split(" ", 1)[0]
        for line in (ground(view, retriever, len(view.action_space)).grounding or [])
    ]
    positions = {fid: i for i, fid in enumerate(ranked_ids)}
    n = len(ranked_ids)

    path = args.out / "all.answers.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    hits = [positions[c] for r in rows for c in dict.fromkeys(r.get("cited", [])) if c in positions]
    if not hits:
        print("no citations to score")
        return
    top = sum(1 for h in hits if h < args.keep)
    print(f"citations: {len(hits)} across {len(rows)} decisions, {n} facts ranked")
    print(f"in the top {args.keep}: {top}/{len(hits)} = {top / len(hits):.2f}")
    print(f"expected if ranking were noise: {args.keep / n:.2f}")
    print(f"mean rank: {sum(hits) / len(hits):.2f}  (0 = ranked first)")
    for fid in sorted({ranked_ids[h] for h in hits}, key=lambda f: positions[f]):
        print(f"  rank {positions[fid]}  {fid}  ×{sum(1 for h in hits if h == positions[fid])}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["prompts", "score", "predict"])
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / "na-373")
    ap.add_argument("--links", type=Path, default=Path("/home/user/thinker/docs/alphax.txt"))
    ap.add_argument("--keep", type=int, default=4)
    args = ap.parse_args()
    {"prompts": cmd_prompts, "score": cmd_score, "predict": cmd_predict}[args.cmd](args)


if __name__ == "__main__":
    main()
