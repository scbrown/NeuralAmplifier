"""na-xmd — does the model change the answer on byte-identical prompts? (aegis-5xi)

Every measured eval and game before this one was answered by `claude-haiku-4-5`, or by the
claude-code lane, which never recorded which model it ran. So "the previous models hit a wall"
was not a testable claim: no eval had ever put two models in front of the same input.

This eval holds the input fixed by construction. It re-scores na-htm's COMMITTED task files,
which are pinned here by full sha256, against answers from more than one model. It has no
prompts of its own on purpose. Regenerating them would compare models on prompts the earlier
model never saw, and na-htm's prompts are already older than the current `_SYSTEM`
(`just eval check na-htm` names the drift). That is irrelevant to a model comparison and fatal
to one that silently regenerated.

Stdlib only, so `scripts/score_published.py` can re-derive it on a host with nothing installed:

    python evals/cross_model.py evals/runs/na-xmd
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
SOURCE = RUNS / "na-htm"
NEEDS_LINKS = False


def _manifest(out: Path) -> dict[str, Any]:
    return json.loads((out / "manifest.json").read_text())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legal(task: Path) -> set[str]:
    """Action ids the world view offered, read from the exact bytes the models answered."""
    body = task.read_text().rsplit("\n---\n", 1)[0]
    world_view = json.loads(body[body.index("\n{") + 1 :])
    return {a["id"] for a in world_view.get("action_space") or []}


def _answers(path: Path) -> list[dict[str, Any] | None]:
    """One reply per line; an unparseable line is kept as None so it counts against the model."""
    rows: list[dict[str, Any] | None] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            value = None
        rows.append(value if isinstance(value, dict) else None)
    return rows


def _fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Exact two-sided Fisher p for [[a, b], [c, d]]: sum of tables no likelier than observed."""
    n, row, col = a + b + c + d, a + b, a + c

    def p(x: int) -> float:
        return math.comb(row, x) * math.comb(n - row, col - x) / math.comb(n, col)

    observed = p(a)
    lo, hi = max(0, col - (n - row)), min(row, col)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= observed * (1 + 1e-9)))


def stats(answers: list[dict[str, Any] | None], legal: set[str], offered: set[str]) -> dict[str, Any]:
    choices = [a.get("choice") for a in answers if a]
    modal, held = Counter(choices).most_common(1)[0] if choices else (None, 0)
    cited = [a.get("cited") or [] for a in answers if a]
    return {
        "n": len(answers),
        "legal": sum(c in legal for c in choices),
        "modal": modal,
        "held": held,
        "split": dict(Counter(choices)),
        "cites_per_run": round(sum(map(len, cited)) / max(1, len(cited)), 2),
        "invented_cites": sum(1 for row in cited for c in row if c not in offered),
    }


def table(out: Path) -> dict[str, Any]:
    """Every arm x model, plus a pooled stability comparison of each model against the first."""
    manifest = _manifest(out)
    models = manifest["models"]
    rows: dict[str, dict[str, Any]] = {}
    for arm, pin in manifest["arms"].items():
        task = SOURCE / f"{arm}.task.txt"
        if _sha(task) != pin["task_sha256"]:
            raise SystemExit(f"{arm}: na-htm task bytes changed since these answers were recorded")
        legal = _legal(task)
        offered = set(json.loads((SOURCE / f"{arm}.offered.json").read_text()))
        rows[arm] = {
            model: stats(_answers(RUNS / spec["answers"].format(arm=arm)), legal, offered)
            for model, spec in models.items()
        }
    names = list(models)
    pooled: dict[str, Any] = {}
    base = names[0]
    for other in names[1:]:
        for scope, arms in manifest.get("pools", {"all": list(rows)}).items():
            bh = sum(rows[a][base]["held"] for a in arms)
            bn = sum(rows[a][base]["n"] for a in arms)
            oh = sum(rows[a][other]["held"] for a in arms)
            on = sum(rows[a][other]["n"] for a in arms)
            pooled[f"{other} vs {base} [{scope}]"] = {
                base: f"{bh}/{bn}",
                other: f"{oh}/{on}",
                "fisher_p": round(_fisher_two_sided(oh, on - oh, bh, bn - bh), 5),
            }
    return {"rows": rows, "pooled": pooled}


def score(out: Path, links: Path | None = None) -> None:
    result = table(out)
    names = list(_manifest(out)["models"])
    print(f"{'arm':32s} " + " | ".join(f"{m}: legal held modal cites invented" for m in names))
    for arm, by_model in result["rows"].items():
        cells = []
        for m in names:
            s = by_model[m]
            cells.append(
                f"{s['legal']}/{s['n']} {s['held']}/{s['n']} {s['modal']} {s['cites_per_run']} {s['invented_cites']}"
            )
        print(f"{arm:32s} " + " | ".join(cells))
    print()
    for label, row in result["pooled"].items():
        print(f"modal stability {label}: {row}")


def check(links: Path | None = None) -> int:
    """Stale count: the pinned na-htm bytes must be unchanged, and every answer file present."""
    out = RUNS / "na-xmd"
    manifest = _manifest(out)
    stale = 0
    for arm, pin in manifest["arms"].items():
        task = SOURCE / f"{arm}.task.txt"
        if not task.exists() or _sha(task) != pin["task_sha256"]:
            print(f"na-xmd {arm}: STALE — na-htm task bytes moved; these answers are for other input")
            stale += 1
            continue
        missing = [m for m, spec in manifest["models"].items()
                   if not (RUNS / spec["answers"].format(arm=arm)).exists()]
        print(f"na-xmd {arm}: " + (f"MISSING answers for {missing}" if missing else "ok"))
        stale += bool(missing)
    return stale


def selftest() -> int:
    """The statistic and the counters against cases with known answers."""
    failures = 0

    def check_(label: str, got: Any, want: Any) -> None:
        nonlocal failures
        ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-4
        print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
        failures += not ok

    check_("fisher [[3,1],[1,3]]", _fisher_two_sided(3, 1, 1, 3), 0.48571)
    check_("fisher identical arms", _fisher_two_sided(5, 5, 5, 5), 1.0)
    check_("fisher 37/40 vs 22/40", round(_fisher_two_sided(37, 3, 22, 18), 5), 0.00024)
    s = stats([{"choice": "a", "cited": ["x"]}, {"choice": "a", "cited": ["zz"]}, {"choice": "q"}, None],
              legal={"a", "b"}, offered={"x"})
    check_("illegal and unparsed both count against legality", (s["legal"], s["n"]), (2, 4))
    check_("modal held", (s["modal"], s["held"]), ("a", 2))
    check_("an id never offered is an invented citation", s["invented_cites"], 1)
    return 1 if failures else 0


def arms(links: Path | None = None) -> dict[str, Any]:
    raise SystemExit(
        "na-xmd has no prompts of its own: it re-scores na-htm's committed task files, pinned by"
        " sha256 in its manifest. Answer those bytes with a new model and add it to the manifest."
    )


if __name__ == "__main__":
    score(Path(sys.argv[1]) if len(sys.argv) > 1 else RUNS / "na-xmd")
