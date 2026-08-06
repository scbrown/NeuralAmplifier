"""na-htm — does ranking grounding by information value predict what a model cites?

The question na-373 asked and could not answer. It measured **one** world view, reported that
the ranking predicted citation worse than chance, and withdrew the finding on re-measurement:
19 of its 27 baseline citations were ``fac:network-node``, so "citations in the top k" was
really "did network-node make the cut". A statistic over one decision's fact pool is a statistic
about the fact that dominates it.

So this eval changes what is pooled, not how the rule is scored:

**Several world views.** Four decisions captured from real games — three surfaces, two turns,
three factions. Their fact pools are near-disjoint (``unit:``/``res:``/``fac:``, ``soc:``,
``tech:``), which is what stops any single fact from owning the pooled number. The eval
*measures* that rather than asserting it: :func:`dominance` reports the largest share any one
fact and any one decision holds, and :func:`score` refuses to print a headline when either is
past :data:`DOMINANCE_LIMIT`. That check is the whole reason this file exists, and it is the
one thing here that must not be quietly relaxed.

**Aggregate citation rank, not a choice flip.** Every citation contributes its rank within its
*own* decision's ranking, and the two headline numbers pool those ranks: mean reciprocal rank,
and the fraction landing in the top :data:`KEEP`. Both are compared against the null a random
ranking would give, computed per decision and pooled the same way — the decisions have 5 to 8
facts each, so a single chance baseline would be wrong for all of them.

Arms, as in na-373:

``all``     every fact the action space pulls, in retriever order. What ships.
``ranked``  the same facts scored by ``information_value`` and cut to ``KEEP``.

``all`` carries the measurement. ``ranked`` is the falsifier: the rule could rank citation well
and still damage the decision by leaving options unargued, and choice agreement between the arms
is the only place that shows up.

**The grounding is pinned, deliberately.** ``runs/na-htm/grounding.json`` holds the exact facts
the arms were measured with, harvested once from a live ``quipu-server``. Deriving them at score
time is how na-61c2's arms changed underneath it — a retriever behaviour was reverted, the same
call started meaning something else, and the old answers stayed put and kept being scored.
``just eval harvest na-htm`` re-runs the retriever and *prints the diff* without applying it, so
divergence between the pin and what ships is visible rather than automatic.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from harness import RUNS, load_answers, spread, tally
from retrieval_ranking import KEEP, label_of, rank_lines, score_distribution

#: This eval reads no rulebook: its decisions are captured world views and its grounding is
#: pinned, so ``prompts`` needs neither the Thinker checkout nor a live Quipu.
NEEDS_LINKS = False

#: Captured world views live here — real payloads from real games, not hand-written fixtures.
CAPTURED = (
    Path(__file__).resolve().parent.parent / "orchestrator" / "tests" / "fixtures" / "captured"
)

#: The pinned grounding, and the decision set it defines. See the module docstring.
PINNED = RUNS / "na-htm" / "grounding.json"

#: Past this share, the pooled number is about one fact (or one decision) again, which is
#: exactly what na-373 turned out to be. Half is generous — na-373's dominant fact held 0.70.
DOMINANCE_LIMIT = 0.50

#: Below this many citing runs a cell's rate is not a rate. Reported, never silently pooled:
#: a decision that produced no citations and a decision whose citations all ranked badly are
#: different findings, and a pooled percentage renders them identically.
THIN = 3


def decisions() -> dict[str, dict[str, Any]]:
    """The pinned decisions, keyed by capture stem."""
    if not PINNED.exists():
        raise SystemExit(f"no pinned grounding at {PINNED} — run `just eval harvest na-htm`")
    return json.loads(PINNED.read_text())


#: Fields the OBSERVATION carries and the DECISION REQUEST does not — stripped here for the
#: same reason ``hurry_grounding.OBSERVE_ONLY`` exists, and now for the same surfaces.
#:
#: The captures under ``fixtures/captured/`` are adapter *records*, and a record carries the
#: deterministic tier's answer on purpose: it is what "llm chose X, applied Y" is measured
#: against. A request must not carry it. Until na-glk the adapter built both from one buffer
#: and every decide surface but base.hurry sent the native answer to the model, so these
#: prompts were faithful to a production that anchored the brain. na-glk fixed the adapter on
#: all three (base.production, faction.tech, faction.se), which makes the unstripped prompt
#: the unfaithful one — hence this strip, not despite the fix but because of it.
#:
#: ``upheaval_cost`` goes with faction.se's pair: it prices the native move specifically, so
#: keeping it would leave a priced hint at the answer the rest of this tuple removes.
OBSERVE_ONLY = ("native_choice", "native_choice_name", "upheaval_cost", "tier", "applied")


def _world_view(spec: dict[str, Any], grounding: list[str]) -> Any:
    """The captured world view with grounding injected, as ``orchestrator.decide`` injects it."""
    import sys

    sys.path.insert(0, str(CAPTURED.parents[3] / "orchestrator" / "src"))
    from neural_amplifier.contract import WorldView  # noqa: PLC0415

    raw = json.loads((CAPTURED / spec["capture"]).read_text())
    # Strip before validate, not after: `contract._Model` sets extra="allow", so an unknown
    # key survives model_validate and reaches the prompt through model_dump_json.
    view = WorldView.model_validate({k: v for k, v in raw.items() if k not in OBSERVE_ONLY})
    return view.model_copy(update={"grounding": grounding})


def arms(links: Path | None = None, keep: int = KEEP) -> dict[str, Any]:
    """One world view per decision per arm, named ``<decision>.<arm>``.

    ``links`` is accepted and ignored: the rulebook is what the *datalinks* retriever reads, and
    these decisions are grounded through Quipu, which spans surfaces the rulebook path cannot
    ground at all (social engineering, technologies). Keeping the parameter lets ``run.py`` treat
    every eval alike.
    """
    out: dict[str, Any] = {}
    for name, spec in decisions().items():
        full = list(spec["grounding"])
        out[f"{name}.all"] = _world_view(spec, full)
        out[f"{name}.ranked"] = _world_view(spec, rank_lines(full)[:keep])
    return out


# --- scoring -------------------------------------------------------------------------------


def _null_top_k(n: int, keep: int) -> float:
    """P(a citation lands in the top *keep*) if the ranking carried no information."""
    return min(keep, n) / n


def _null_mrr(n: int) -> float:
    """E[1/(rank+1)] over a uniformly random rank — the harmonic mean position, analytically.

    Analytic rather than simulated so the null is exact and needs no seed. A simulated null
    would put a random number in the denominator of every verdict this file prints.
    """
    return sum(1.0 / i for i in range(1, n + 1)) / n


def _wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval on a proportion, Wilson rather than normal.

    The normal approximation is worst exactly where this eval lives — few observations, rates
    near 0 or 1 — and it happily reports intervals that run past the ends of the scale. An eval
    filed *because* an underpowered number was believed does not get to use the interval that
    flatters small samples.
    """
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def _citations(out: Path, name: str, arm: str, offered: list[str]) -> tuple[list[list[str]], int]:
    """Per-run citation lists for one cell, filtered to what was actually offered.

    Returns the per-run lists (including the empty ones) and the run count. The empty runs are
    kept on purpose: dropping them turns "the model cited nothing on two runs in three" into a
    confident rate over the third, which is the shape of failure this eval exists to avoid.
    """
    offered_set = set(offered)
    rows = load_answers(out, f"{name}.{arm}")
    per_run = [
        [c for c in dict.fromkeys(row.get("cited", []) or []) if c in offered_set] for row in rows
    ]
    return per_run, len(rows)


def dominance(pooled: list[tuple[str, str]]) -> dict[str, Any]:
    """Is the pooled statistic about a ranking rule, or about one fact again?

    ``pooled`` is one ``(decision, fact_id)`` per citation. Two ways the pool can collapse, and
    na-373 only ever checked itself against neither:

    * one **fact** supplies most citations — na-373's actual defect;
    * one **decision** supplies most citations, which makes "pooled over four decisions" a
      description of the sampling rather than of the measurement.
    """
    if not pooled:
        return {"citations": 0}
    facts: dict[tuple[str, str], int] = {}
    decs: dict[str, int] = {}
    for decision, fid in pooled:
        facts[(decision, fid)] = facts.get((decision, fid), 0) + 1
        decs[decision] = decs.get(decision, 0) + 1
    (top_dec, top_fid), top_fact_n = max(facts.items(), key=lambda kv: kv[1])
    top_decision, top_decision_n = max(decs.items(), key=lambda kv: kv[1])
    n = len(pooled)
    return {
        "citations": n,
        "top_fact": f"{top_fid} ({top_dec})",
        "top_fact_share": top_fact_n / n,
        "top_decision": top_decision,
        "top_decision_share": top_decision_n / n,
        "decisions_contributing": len(decs),
    }


def score(out: Path, links: Path | None = None, keep: int = KEEP) -> None:
    specs = decisions()

    print(f"{'decision':28} {'surf':16} {'turn':>4} {'opts':>5} {'facts':>6}  fact ids")
    for name, spec in specs.items():
        ids = [line.split(" ", 1)[0] for line in spec["grounding"]]
        kinds = sorted({i.split(":")[0] for i in ids})
        print(
            f"{name:28} {spec['surface_id']:16} {spec['turn']:>4} {spec['options']:>5} "
            f"{len(ids):>6}  {'/'.join(kinds)}"
        )
    shared = _shared_facts(specs)
    print(
        f"\nfacts shared between decisions: {len(shared)}"
        + (f"  ({', '.join(sorted(shared))})" if shared else "  — the pools are disjoint")
    )

    # --- per cell: did anything get cited at all -------------------------------------------
    print(
        f"\n{'decision':28} {'arm':7} {'runs':>5} {'citing':>7} {'cites':>6} {'util':>6}  choices"
    )
    pooled: list[tuple[str, str]] = []
    ranks: list[tuple[str, int, int]] = []  # (decision, rank, n_facts)
    modal: dict[str, float] = {}
    choices: dict[tuple[str, str], dict[str, int]] = {}
    empty_cells: list[str] = []
    for name, spec in specs.items():
        full = list(spec["grounding"])
        order = [ln.split(" ", 1)[0] for ln in rank_lines(full)]
        position = {fid: i for i, fid in enumerate(order)}
        for arm in ("all", "ranked"):
            offered = [
                ln.split(" ", 1)[0] for ln in (full if arm == "all" else rank_lines(full)[:keep])
            ]
            per_run, runs = _citations(out, name, arm, offered)
            if not runs:
                empty_cells.append(f"{name}.{arm}")
                continue
            citing = sum(1 for r in per_run if r)
            cites = sum(len(r) for r in per_run)
            util = statistics.mean(len(r) / len(offered) for r in per_run) if offered else 0.0
            rows = load_answers(out, f"{name}.{arm}")
            counts = tally(rows)
            choices[(name, arm)] = counts
            if arm == "all":
                modal[name] = max(counts.values()) / len(rows) if rows else 0.0
                for run in per_run:
                    for fid in run:
                        pooled.append((name, fid))
                        ranks.append((name, position[fid], len(order)))
            flag = "  <- thin" if citing < THIN else ""
            print(
                f"{name:28} {arm:7} {runs:>5} {citing:>7} {cites:>6} {util:>6.2f}  "
                f"{spread(counts)}{flag}"
            )
    if empty_cells:
        print(f"\nno answers for {len(empty_cells)} cell(s): {', '.join(empty_cells)}")

    # A decision the model answers the same way every time cannot show a ranking doing damage.
    # na-373's `base.production` sat at 19/20 for one option; reporting that alongside the
    # ranking result is what makes "contested" an observation instead of an assumption.
    if modal:
        print("\ncontestedness of the baseline arm (modal share — 1.00 cannot show damage):")
        for name, share in sorted(modal.items(), key=lambda kv: -kv[1]):
            print(f"  {share:.2f}  {name}")

    if not ranks:
        # Unanswered and answered-but-uncited are different states and must never print the
        # same line. A run nobody has answered is not evidence about the ranking rule; a run
        # the model answered while citing nothing is evidence about the grounding being read.
        # Rendering both as "no citations" is the green-signal-over-a-dead-backend failure.
        if not choices:
            print("\nNOT RUN — no answers are committed for any cell. Nothing has been measured.")
            print(f"Answer the eight <cell>.task.txt files under {out} and collect the replies.")
        else:
            print("\nANSWERED, BUT NOTHING CITED — every committed answer cited no offered fact.")
            print("This is not a null result about the ranking rule: it says the grounding was")
            print("not read at all. Check the run's guard advisories before reading it either way.")
        return

    # --- the headline, gated on dominance ---------------------------------------------------
    dom = dominance(pooled)
    print(f"\ndominance of the pooled citations ({dom['citations']} citations):")
    print(f"  largest single fact      {dom['top_fact_share']:.2f}  {dom['top_fact']}")
    print(f"  largest single decision  {dom['top_decision_share']:.2f}  {dom['top_decision']}")
    print(f"  decisions contributing   {dom['decisions_contributing']} of {len(specs)}")

    collapsed = max(dom["top_fact_share"], dom["top_decision_share"]) >= DOMINANCE_LIMIT
    if collapsed:
        print(
            f"\nREFUSING THE HEADLINE — one fact or one decision holds "
            f"{max(dom['top_fact_share'], dom['top_decision_share']):.2f} of the citations,"
            f" past the {DOMINANCE_LIMIT:.2f} limit."
        )
        print("That is na-373's defect reproduced, not a result about the ranking rule. The")
        print("numbers below are printed for diagnosis and must not be quoted as a finding.")

    top = sum(1 for _, r, _ in ranks if r < keep)
    n = len(ranks)
    lo, hi = _wilson(top, n)
    null_top = statistics.mean(_null_top_k(nf, keep) for _, _, nf in ranks)
    mrr = statistics.mean(1.0 / (r + 1) for _, r, _ in ranks)
    null_mrr = statistics.mean(_null_mrr(nf) for _, _, nf in ranks)

    print(f"\naggregate citation rank, pooled over {dom['decisions_contributing']} decisions:")
    print(f"  fraction in top {keep}   {top}/{n} = {top / n:.2f}   95% CI [{lo:.2f}, {hi:.2f}]")
    print(f"  a ranking with no information would give   {null_top:.2f}")
    print(f"  mean reciprocal rank  {mrr:.3f}   chance {null_mrr:.3f}")

    if not collapsed:
        if lo <= null_top <= hi:
            need = _needed(top / n, null_top)
            print(
                f"\nVERDICT: cannot distinguish from chance at n={n}. The interval covers the"
                f" null.\n  Separating an effect this size needs roughly {need} pooled citations."
            )
        elif lo > null_top:
            print("\nVERDICT: the ranking puts cited facts higher than chance.")
        else:
            print(
                "\nVERDICT: the ranking puts cited facts LOWER than chance — it is anti-predictive."
            )

    # Where the citations actually sat, per decision. The pooled number can be flat while one
    # surface is strongly predicted and another is inverted, and only this shows that.
    print("\nrank of each citation, by decision:")
    for name in specs:
        mine = [r for d, r, _ in ranks if d == name]
        if not mine:
            print(f"  {name:28} — no citations")
            continue
        nf = next(nf for d, _, nf in ranks if d == name)
        print(
            f"  {name:28} n={len(mine):<3} ranks {sorted(mine)}  of {nf}  "
            f"(top{keep} {sum(1 for r in mine if r < keep)}/{len(mine)}, "
            f"chance {_null_top_k(nf, keep):.2f})"
        )

    # The falsifier. A rule can rank citation well and still hurt: grounding is roughly one
    # fact per option, so truncating removes the argument for a particular option rather than
    # removing information, and an unexplained option loses.
    agree = [
        (name, choices.get((name, "all"), {}), choices.get((name, "ranked"), {}))
        for name in specs
        if (name, "ranked") in choices and (name, "all") in choices
    ]
    if agree:
        print(f"\ndid truncating to {keep} move the decision:")
        for name, a, r in agree:
            a_top = max(a, key=a.get) if a else "?"
            r_top = max(r, key=r.get) if r else "?"
            verdict = "same modal choice" if a_top == r_top else "MOVED"
            print(f"  {name:28} all={a_top!r:>18} ranked={r_top!r:>18}  {verdict}")


def _shared_facts(specs: dict[str, dict[str, Any]]) -> set[str]:
    seen: dict[str, set[str]] = {}
    for name, spec in specs.items():
        for line in spec["grounding"]:
            seen.setdefault(line.split(" ", 1)[0], set()).add(name)
    return {fid for fid, where in seen.items() if len(where) > 1}


def _needed(observed: float, null: float, z: float = 1.96) -> int:
    """Roughly how many pooled citations would put the null outside the interval."""
    gap = abs(observed - null)
    if gap < 1e-6:
        return 0
    return max(1, math.ceil((z * z * observed * (1 - observed)) / (gap * gap)))


# --- the control this eval's own history demands ---------------------------------------------


def selftest() -> int:
    """Does the scorer recover a known answer? Run before believing any number it prints.

    na-373 published a statistic nobody had checked against a case with a known result. Every
    assertion here is a case where the right answer is arithmetic rather than a measurement.
    """
    failures = []

    def check(label: str, got: Any, want: Any, tol: float = 1e-9) -> None:
        ok = abs(got - want) <= tol if isinstance(want, float) else got == want
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
        if not ok:
            failures.append(label)

    # Nulls, against values that can be computed by hand.
    check("null top-4 of 8 facts", _null_top_k(8, 4), 0.5)
    check(
        "null top-4 of 4 facts",
        _null_top_k(4, 4),
        1.0,
    )
    check("null MRR of 2 facts", _null_mrr(2), (1 / 1 + 1 / 2) / 2)
    check("null MRR of 1 fact", _null_mrr(1), 1.0)

    # Wilson: a proportion at the boundary must not report an interval off the scale, which is
    # precisely where the normal approximation does.
    lo, hi = _wilson(0, 5)
    check("wilson lower bound at 0/5 is not negative", lo >= 0.0, True)
    check("wilson upper bound at 0/5 is below 1", hi < 1.0, True)

    # Dominance: the na-373 shape must trip the limit, and a spread pool must not.
    na373 = [("d", "fac:network-node")] * 19 + [("d", f"fac:other-{i}") for i in range(8)]
    dom = dominance(na373)
    check("na-373's pool is flagged dominated", dom["top_fact_share"] >= DOMINANCE_LIMIT, True)
    check("na-373's pool has one contributing decision", dom["decisions_contributing"], 1)
    spread_pool = [(f"d{i}", f"x:{i}-{j}") for i in range(4) for j in range(5)]
    dom2 = dominance(spread_pool)
    check("a spread pool is not flagged", dom2["top_fact_share"] < DOMINANCE_LIMIT, True)
    check("a spread pool counts all decisions", dom2["decisions_contributing"], 4)
    check("one decision's share of a balanced pool", dom2["top_decision_share"], 0.25)

    # An empty pool must not raise or claim anything.
    check("an empty pool reports zero citations", dominance([])["citations"], 0)

    # The ranking rule itself, on a case where the intended order is obvious: a fact carrying a
    # number the option's own name does not is more informative than the bare name.
    ordered = rank_lines(
        [
            "a:one One — cost 8; upkeep 1/turn; Labs Bonus",
            "b:two Two",
        ]
    )
    check("the informative line ranks first", ordered[0].split(" ", 1)[0], "a:one")

    # ...and the same case in the format a REAL retriever emits. The check above uses na-373's
    # em-dash fixture, and passing it is what let na-5to hide: `label_of` split on " — " only, so
    # on semicolon-separated facts it returned the whole line as the option's name, `known`
    # swallowed every content word, and the score collapsed to the id tokens. It scored the 40
    # facts pinned below into TWO distinct values while this check stayed green. A guard catches
    # the failure it was built for — so both formats are exercised, and the name is asserted
    # directly rather than inferred from an ordering that a flat rule still produces.
    check("name ends at the em dash", label_of("a:one One — cost 8; Labs Bonus"), "One")
    check("name ends at the semicolon", label_of("a:one One; cost 8; Labs Bonus"), "One")
    check(
        "the earliest separator wins, not the first listed",
        label_of("fac:rt Recycling Tanks — cost 4; Bonus Resources"),
        "Recycling Tanks",
    )
    ordered_real = rank_lines(
        [
            "a:one One; cost 8; upkeep 1/turn; Labs Bonus",
            "b:two Two",
        ]
    )
    check("the informative line ranks first on real grounding", ordered_real[0][:5], "a:one")

    # The rule's DISCRIMINATION, measured on the real pinned facts rather than on a fixture.
    # This is the check that would have caught na-5to, and the only one that can: `sorted` is
    # stable, so a rule scoring everything alike still returns a plausible-looking ranking — the
    # arm silently degrades to retriever order and nothing downstream can tell. Assert on the
    # score distribution, never on the output shape.
    pinned = [ln for spec in decisions().values() for ln in spec["grounding"]]
    spread_scores = score_distribution(pinned)
    check("the pinned facts are the ones na-htm measures", len(pinned) > 30, True)
    check(
        f"the ranking rule discriminates ({len(spread_scores)} distinct scores over "
        f"{len(pinned)} facts)",
        len(spread_scores) > 2,
        True,
    )

    # Sample-size arithmetic: a bigger gap must need fewer observations, never more.
    check(
        "a wide gap needs less data than a narrow one", _needed(0.9, 0.5) < _needed(0.55, 0.5), True
    )

    # The answer key must not be in the prompt that asks the question. Asserted on the PROMPT
    # TEXT, not on the stripped dict: `contract._Model` sets extra="allow", so a field survives
    # model_validate and reaches the model through model_dump_json — checking the dict would
    # pass while the prompt still carried it. Same check hurry_grounding makes, now that na-glk
    # has made all three of these surfaces withhold it in production too.
    from harness import task_text  # noqa: PLC0415

    # Matched as a JSON KEY (`"tier":`), not as a bare substring: "tier" occurs inside
    # `soc:politics-frontier`, so the substring form failed on a prompt that was already clean.
    for name, view in arms().items():
        text = task_text(view)
        for field in OBSERVE_ONLY:
            check(f'{name} prompt has no "{field}" key', f'"{field}":' in text, False)

    # The pin must describe decisions that can actually carry the measurement.
    specs = decisions()
    check("more than one decision is pinned", len(specs) > 1, True)
    for name, spec in specs.items():
        check(f"{name} offers more than one option", spec["options"] > 1, True)
        check(f"{name} grounds to at least one fact", len(spec["grounding"]) > 0, True)
    check("no fact is shared by every decision", len(_shared_facts(specs)) < 27, True)

    # End to end, against synthetic runs whose right answer is known before the scorer sees
    # them. The unit checks above cannot catch a scorer that computes each part correctly and
    # assembles them wrongly — which is the only kind of error na-373 could have had.
    print("\n  end-to-end, on runs with a known answer:")
    for label, report, wanted, unwanted in _scorer_controls(specs):
        for want in wanted:
            check(f"{label}: says {want!r}", want in report, True)
        for avoid in unwanted:
            check(f"{label}: does not say {avoid!r}", avoid in report, False)

    print(f"\n{len(failures)} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


def _scorer_controls(specs: dict[str, dict[str, Any]]) -> list[tuple[str, str, list, list]]:
    """Run :func:`score` over fabricated answers and return what it printed.

    Four cases, each chosen because a plausible bug would fail exactly one of them:

    ``perfect``      every citation at rank 0 — the scorer must find the effect it is for.
    ``na-373``       one fact of one decision, twenty times — the gate must refuse the headline.
    ``inverted``     every citation last — the anti-predictive branch, which is the direction
                     na-373 originally reported and so the one most worth being sure about.
    ``uncited``      answers present, nothing cited — must read as grounding unread, never as a
                     result about the ranking. This is the case that looks like a finding.
    """
    import contextlib
    import io
    import tempfile

    def run(build) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            build(out)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                score(out)
            return buffer.getvalue()

    def ordering(spec: dict[str, Any]) -> list[str]:
        return [ln.split(" ", 1)[0] for ln in rank_lines(list(spec["grounding"]))]

    def every_decision(pick, runs: int = 6):
        def build(out: Path) -> None:
            for name, spec in specs.items():
                cited = pick(ordering(spec))
                rows = [json.dumps({"choice": "build:0", "cited": cited}) for _ in range(runs)]
                (out / f"{name}.all.answers.jsonl").write_text("\n".join(rows) + "\n")

        return build

    def one_decision(out: Path) -> None:
        name = next(iter(specs))
        top = ordering(specs[name])[0]
        rows = [json.dumps({"choice": "build:0", "cited": [top]}) for _ in range(20)]
        (out / f"{name}.all.answers.jsonl").write_text("\n".join(rows) + "\n")

    return [
        (
            "perfect",
            run(every_decision(lambda order: [order[0]])),
            ["= 1.00", "higher than chance"],
            ["REFUSING THE HEADLINE", "anti-predictive"],
        ),
        (
            "na-373",
            run(one_decision),
            ["REFUSING THE HEADLINE", "largest single decision  1.00"],
            ["VERDICT"],
        ),
        (
            "inverted",
            run(every_decision(lambda order: [order[-1]])),
            ["anti-predictive", "= 0.00"],
            ["REFUSING THE HEADLINE"],
        ),
        (
            "uncited",
            run(every_decision(lambda _order: [])),
            ["ANSWERED, BUT NOTHING CITED"],
            ["VERDICT", "NOT RUN", "aggregate citation rank"],
        ),
    ]


def harvest(quipu_url: str = "http://127.0.0.1:3030") -> int:
    """Re-run the retriever against a live Quipu and report how the pin has drifted.

    Prints; does not write. The pin is the experiment — replacing it silently is how na-61c2's
    arms changed underneath answers that stayed put. Applying a diff is a decision with a
    re-measurement attached, so it stays manual.
    """
    import sys

    sys.path.insert(0, str(CAPTURED.parents[3] / "orchestrator" / "src"))
    from neural_amplifier.contract import WorldView  # noqa: PLC0415
    from neural_amplifier.datalinks import QuipuRetriever  # noqa: PLC0415

    specs = decisions()
    drift = 0
    for name, spec in specs.items():
        view = WorldView.model_validate(json.loads((CAPTURED / spec["capture"]).read_text()))
        try:
            g = QuipuRetriever(quipu_url, engine=view.engine).retrieve(view)
        except Exception as exc:  # noqa: BLE001 — a dead server is a reportable outcome here
            print(f"{name}: retriever failed — {exc}")
            drift += 1
            continue
        now = [f"{i} {t}" for i, t in zip(g.fact_ids, g.facts, strict=True)]
        if now == spec["grounding"]:
            print(f"{name}: ok ({len(now)} facts)")
            continue
        drift += 1
        print(f"{name}: DRIFTED — pinned {len(spec['grounding'])} facts, live gives {len(now)}")
        for line in sorted(set(spec["grounding"]) - set(now)):
            print(f"    - {line[:100]}")
        for line in sorted(set(now) - set(spec["grounding"])):
            print(f"    + {line[:100]}")
    if drift:
        print(
            f"\n{drift} decision(s) drifted. The committed answers were measured against the"
            " pin, so re-pinning means re-answering — do not do one without the other."
        )
    return 1 if drift else 0
