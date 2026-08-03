"""na-qu8 — does grounding base.hurry's subject move its stability, and toward what?

base.hurry measured 0.60 stability, the least stable surface we have, and the stated cause was
that it grounds to nothing: neither action label ("Hurry production", "Do not hurry") is a node
in any graph. The adapter now names the item being rushed in ``subjects``, so the surface can
ground on *what is being hurried* instead. Nothing had re-measured it, which made "grounding the
subject will help" an inference from a plausible mechanism — the same shape as the na-373 ranking
argument that did not survive contact with a measurement.

Arms:

``bare``     the world view as it ships, no grounding.
``subject``  the same, plus the one fact the subject retrieves.

**Stability is not the score, and must never be read alone.** This surface SPENDS: hurrying
Gaia's Landing's Colony Pod costs 19 credits out of 171, irreversibly, to save two turns. A
grounding change that made the decision perfectly stable on the *wrong* answer would look like a
triumph on a stability number. So :func:`score` reports stability and agreement with the
deterministic tier side by side, and refuses a verdict on stability alone — the same discipline
na-373 needed when utilisation alone rewarded offering less.

**The prompt strips three fields the capture carries, and the reason is not cosmetic.** The
fixture came from ``na-observations.jsonl``, which is an OBSERVATION record; the request body the
engine actually POSTs is the same bytes *minus the outcome fields appended after the call
returns* (``thinker/src/neural.cpp:1352``). For this surface those outcome fields include
``native_choice`` — ``na_decide_base_hurry`` passes ``native_hurried = -1`` precisely so the
decision request withholds it (``:2288``, ``:2235``). Leaving it in would put the deterministic
tier's answer in front of a model we are about to ask for an independent one, which would not
merely be unfaithful to production: it would destroy this eval's own falsifier, since agreement
with an answer you were shown is not agreement.

An observation record is not a decision request. That is the third time this suite has been bitten
by a fixture that was not the payload (na-vbe, na-61c2, now this), and it is the first time the
difference was the answer itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness import load_answers, spread, tally
from multi_decision_ranking import CAPTURED, _wilson

#: This eval reads no rulebook and needs no live server — the capture and its grounding are both
#: committed. See ``multi_decision_ranking`` for why the grounding is pinned rather than derived.
NEEDS_LINKS = False

#: The recovered capture. Turn 42, Gaians, Gaia's Landing hurrying a Colony Pod.
CAPTURE = CAPTURED / "base_hurry_turn42.json"

#: The pinned grounding for the ``subject`` arm — what ``QuipuRetriever`` returns for
#: ``subjects: ["Colony Pod"]``, harvested 2026-08-03.
SUBJECT_GROUNDING = (
    "unit:colony-pod Colony Pod; founds a new base elsewhere; does not grow the base that"
    " builds it; [house-rule · src:alphax-txt]",
)

#: Fields the OBSERVATION carries and the DECISION REQUEST does not. Stripped, not renamed:
#: see the module docstring. ``native_choice`` is the eval's own answer key and must not be in
#: the prompt that asks the question.
OBSERVE_ONLY = ("native_choice", "tier", "applied")


#: What the deterministic tier chose, read off the capture rather than restated here, so the
#: answer key cannot drift from the fixture it belongs to.
def native_choice() -> str:
    return str(json.loads(CAPTURE.read_text())["native_choice"])


def _world_view(grounding: tuple[str, ...] | None) -> Any:
    import sys

    sys.path.insert(0, str(CAPTURED.parents[3] / "orchestrator" / "src"))
    from neural_amplifier.contract import WorldView  # noqa: PLC0415

    raw = {k: v for k, v in json.loads(CAPTURE.read_text()).items() if k not in OBSERVE_ONLY}
    view = WorldView.model_validate(raw)
    return view.model_copy(update={"grounding": list(grounding)}) if grounding else view


def arms(links: Path | None = None) -> dict[str, Any]:
    """``links`` is accepted and ignored — this surface grounds through Quipu, not the rulebook."""
    return {"bare": _world_view(None), "subject": _world_view(SUBJECT_GROUNDING)}


def score(out: Path, links: Path | None = None) -> None:
    native = native_choice()
    print(f"the deterministic tier chose {native!r} — what every stability claim is read against\n")
    print(f"{'arm':9} {'runs':>5} {'stable':>7} {'modal':>16} {'agrees':>8} {'cited':>7}  choices")

    summary: dict[str, dict[str, Any]] = {}
    for name in ("bare", "subject"):
        rows = load_answers(out, name)
        if not rows:
            continue
        counts = tally(rows)
        top, top_n = max(counts.items(), key=lambda kv: kv[1])
        agree = sum(1 for r in rows if r.get("choice") == native)
        offered = {SUBJECT_GROUNDING[0].split(" ", 1)[0]} if name == "subject" else set()
        cited = sum(1 for r in rows if {c for c in (r.get("cited") or []) if c in offered})
        summary[name] = {
            "n": len(rows),
            "stability": top_n / len(rows),
            "modal": top,
            "agree": agree / len(rows),
            "cited": cited,
        }
        print(
            f"{name:9} {len(rows):>5} {top_n / len(rows):>10.2f} {top!r:>16} "
            f"{agree / len(rows):>8.2f}  {cited:>3}/{len(rows):<3} {spread(counts)}"
        )

    if len(summary) < 2:
        missing = [a for a in ("bare", "subject") if a not in summary]
        print(f"\nNOT RUN — no answers for: {', '.join(missing)}. Nothing has been measured.")
        return

    bare, subj = summary["bare"], summary["subject"]

    # A two-option surface has a stability FLOOR of 0.50: a coin flip reports 0.50, not 0.
    # Quoting 0.60 without that floor beside it makes an almost-random surface sound decided,
    # which is how "the least stable surface we have" was read as a smaller problem than it was.
    print(
        "\nstability floor on a two-option surface: 0.50 (a coin flip)."
        " Read every figure against that."
    )
    for name, s in summary.items():
        lo, hi = _wilson(round(s["stability"] * s["n"]), s["n"])
        print(f"  {name:9} {s['stability']:.2f}  95% CI [{lo:.2f}, {hi:.2f}]  n={s['n']}")

    moved = subj["stability"] - bare["stability"]
    toward = subj["agree"] - bare["agree"]
    print(
        f"\ngrounding the subject moved stability by {moved:+.2f}, tier agreement by {toward:+.2f}"
    )

    # The two numbers, and the four things they can mean. Printed as a table rather than a
    # verdict sentence because three of the four are not "it worked".
    if abs(moved) < 0.05:
        print("  VERDICT: no material move in stability. Grounding the subject did not settle it.")
    elif moved > 0 and toward >= 0:
        print("  VERDICT: more stable AND no less aligned with the deterministic tier.")
    elif moved > 0 and toward < 0:
        print("  VERDICT: MORE STABLE AND LESS ALIGNED — this is the bad case, not a win.")
        print("  A surface that spends credits has settled harder on the other answer. Whether")
        print("  that is better depends on whether the deterministic tier is right, which this")
        print("  eval does not measure. Do not ship on the stability number alone.")
    else:
        print("  VERDICT: grounding made the decision LESS stable.")

    if subj["cited"] == 0:
        print("\n  TREATMENT WARNING: the subject arm cited the offered fact 0 times. Any")
        print("  difference above is then not a grounding effect — it is run-to-run variance")
        print("  on a surface already measured at near-coin-flip stability.")


def selftest() -> int:
    """Known-answer controls. No model, no server, no run."""
    failures = []

    def check(label: str, got: Any, want: Any) -> None:
        ok = got == want
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
        if not ok:
            failures.append(label)

    a = arms()
    check("two arms", sorted(a), ["bare", "subject"])
    check("the bare arm has no grounding", a["bare"].grounding, None)
    check("the subject arm has exactly one fact", len(a["subject"].grounding or []), 1)
    check("both arms offer the same two options", len(a["bare"].action_space), 2)
    check("the answer key is read off the capture", native_choice(), "hurry:none")

    # The leak control. This is the assertion this eval exists to keep true — if the answer key
    # reaches the prompt, every agreement number is measuring compliance rather than agreement.
    #
    # Asserted on the payload's KEYS, not on the prompt text. The first version substring-matched
    # and failed on `tier`, because the system prompt discusses retrieval tiers — a false positive
    # that would have been "fixed" by deleting the check that works.
    from harness import task_text  # noqa: PLC0415

    for name, view in a.items():
        payload = json.loads(view.model_dump_json())
        for field in OBSERVE_ONLY:
            check(f"{name} payload does not carry {field!r}", field in payload, False)
        # NOT "the answer string is absent". `hurry:none` is one of the two legal action ids and
        # MUST be in the payload — the model cannot pick an option it was not offered. What has to
        # be absent is the assertion that the deterministic tier chose it. Written down because
        # the first version of this check asserted the former, which is unsatisfiable, and the
        # honest fix was to correct the check rather than to weaken the eval.
        check(
            f"{name} still offers the native answer as a choice",
            native_choice() in [a.id for a in view.action_space],
            True,
        )
        check(f"{name} prompt has no native_choice", "native_choice" in task_text(view), False)

    # Both arms must differ ONLY by grounding — otherwise the comparison is not about grounding.
    bare_json = a["bare"].model_dump_json(indent=2)
    subj_json = a["subject"].model_dump_json(indent=2)
    diff_keys = {
        k
        for k in json.loads(bare_json) | json.loads(subj_json)
        if json.loads(bare_json).get(k) != json.loads(subj_json).get(k)
    }
    check("the arms differ only in `grounding`", diff_keys, {"grounding"})

    print(f"\n{len(failures)} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


def harvest(quipu_url: str = "http://127.0.0.1:3030") -> int:
    """Has the pinned subject grounding drifted from what the live retriever serves?"""
    import sys

    sys.path.insert(0, str(CAPTURED.parents[3] / "orchestrator" / "src"))
    from neural_amplifier.contract import WorldView  # noqa: PLC0415
    from neural_amplifier.datalinks import QuipuRetriever  # noqa: PLC0415

    view = WorldView.model_validate(json.loads(CAPTURE.read_text()))
    try:
        g = QuipuRetriever(quipu_url, engine=view.engine).retrieve(view)
    except Exception as exc:  # noqa: BLE001 — a dead server is a reportable outcome
        print(f"retriever failed — {exc}")
        return 1
    now = tuple(f"{i} {t}" for i, t in zip(g.fact_ids, g.facts, strict=True))
    if now == SUBJECT_GROUNDING:
        print(f"subject grounding: ok ({len(now)} fact)")
        print(f"  action labels still unmatched, as the bead's premise requires: {g.unmatched}")
        return 0
    print("DRIFTED —")
    for line in sorted(set(SUBJECT_GROUNDING) - set(now)):
        print(f"    - {line[:100]}")
    for line in sorted(set(now) - set(SUBJECT_GROUNDING)):
        print(f"    + {line[:100]}")
    return 1
