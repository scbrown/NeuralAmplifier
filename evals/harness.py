"""Shared plumbing for the decision evals — see ``evals/README.md``.

An eval here is a **behavioural** question about the brain, not a unit test: does grounding get
read, does a standing plan get followed, does the decision hold still. None of that can be
asserted in `pytest`, because the answer is a distribution over a model's outputs rather than a
value. So the shape is always the same three steps, and this module owns the two boring ones:

1. Build one real world view and emit the exact prompt the brain would send, once per arm.
2. **Something answers them.** Deliberately not this module's business — a paid API run, or
   agents driving the same file. An eval that could only be run one way would not have been run.
3. Score the answers against what the arm was testing.

The prompts and the answers are both committed under ``runs/``, because a number in a document
that cannot be recomputed is an assertion rather than a measurement.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from neural_amplifier.brain import _SYSTEM  # noqa: E402
from neural_amplifier.contract import Action, WorldView  # noqa: E402
from neural_amplifier.datalinks.briefing import DatalinksRetriever  # noqa: E402
from neural_amplifier.datalinks.parse import parse  # noqa: E402

#: Where a run's prompts, answers and manifest live. One directory per eval id.
RUNS = Path(__file__).resolve().parent / "runs"

#: The Thinker fork's rulebook. Not in this repo — it is the sibling checkout's, and the eval
#: needs it only to *regenerate* prompts. Scoring a committed run does not.
DEFAULT_LINKS = Path("../thinker/docs/alphax.txt")

#: The eight options the measured `base.production` decision offered. Real facility names, so
#: they resolve in the datalinks graph — a made-up option would ground to nothing and the eval
#: would be measuring its own fixture.
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


def base_production(links_path: Path) -> tuple[WorldView, DatalinksRetriever]:
    """The world view both evals start from: University Base, turn 35, eight build options."""
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


def ground(
    view: WorldView, retriever: DatalinksRetriever, keep: int | None = None
) -> WorldView:
    """Inject grounding as ``orchestrator.decide`` does — id-first ``"<id> <text>"`` lines."""
    g = retriever.retrieve(view)
    ids, facts = list(g.fact_ids), list(g.facts)
    if keep is not None:
        ids, facts = ids[:keep], facts[:keep]
    return view.model_copy(
        update={"grounding": [f"{i} {t}" for i, t in zip(ids, facts, strict=True)]}
    )


def task_text(world_view: WorldView) -> str:
    """System prompt plus world view plus the answer format, as one file an agent can be given.

    The response format is spelled out here rather than left to structured output because the
    thing answering may not support it. `choice` and `cited` are the two fields every eval so
    far scores; an eval needing more should extend this rather than inventing its own contract.
    """
    return (
        _SYSTEM
        + "\n\n---\n\nWorld view:\n\n"
        + world_view.model_dump_json(indent=2)
        + "\n\n---\n\nRespond with ONLY a single JSON object on one line, no prose, no code"
        ' fence:\n{"choice": "<action_id you pick>", "cited": ["<grounding ids that influenced'
        ' you>"]}\n'
    )


def write_prompts(out: Path, arms: dict[str, WorldView], note: str = "") -> None:
    """Write one task file per arm, plus the offered-fact set and a manifest.

    The manifest carries a hash of each prompt. Answers committed beside a prompt that has since
    changed are not evidence about the current prompt, and without the hash nobody can tell.
    """
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"note": note, "arms": {}}
    for name, view in arms.items():
        text = task_text(view)
        (out / f"{name}.task.txt").write_text(text)
        offered = [line.split(" ", 1)[0] for line in view.grounding or []]
        (out / f"{name}.offered.json").write_text(json.dumps(offered, indent=2))
        manifest["arms"][name] = {
            "prompt_sha256": hashlib.sha256(text.encode()).hexdigest()[:12],
            "facts_offered": len(offered),
            "options": len(view.action_space),
        }
        print(f"  {name}: {len(offered)} facts offered")
    existing = (
        json.loads((out / "manifest.json").read_text())
        if (out / "manifest.json").exists()
        else {}
    )
    manifest["answered_by"] = existing.get("answered_by", "unrecorded")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def check_prompts(out: Path, arms: dict[str, WorldView], label: str = "") -> int:
    """Do the committed answers still belong to the prompt the code produces? Returns stale count.

    A prompt is not a stable thing — the system prompt has changed twice, and once an eval's
    *arms* changed underneath it because they were derived from retriever behaviour that was
    later reverted. Both times the committed answers stayed put and kept being scored, which is
    the failure worth catching: a number that is merely out of date announces nothing, while
    `score` goes on printing it with full confidence.

    This does not judge whether the drift matters. It says what moved, and leaves that to a
    person — some prompt changes cannot affect a given arm, and asserting which is a claim that
    needs its own evidence.
    """
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        print(f"{label}: no manifest — cannot tell whether the run matches the prompt")
        return 1
    manifest = json.loads(manifest_path.read_text())
    stale = 0
    for name, recorded in manifest.get("arms", {}).items():
        if name not in arms:
            print(f"{label} {name}: arm no longer exists in the eval")
            stale += 1
            continue
        now = hashlib.sha256(task_text(arms[name]).encode()).hexdigest()[:12]
        was = recorded.get("prompt_sha256")
        if now == was:
            print(f"{label} {name}: ok")
            continue
        stale += 1
        print(
            f"{label} {name}: STALE — measured against {was}, code now produces {now}"
        )
        committed = out / f"{name}.task.txt"
        if committed.exists():
            diff = list(
                difflib.unified_diff(
                    committed.read_text().splitlines(),
                    task_text(arms[name]).splitlines(),
                    "committed",
                    "now",
                    lineterm="",
                    n=0,
                )
            )
            for line in diff[:12]:
                print(f"    {line}")
            if len(diff) > 12:
                print(f"    ... {len(diff) - 12} more diff lines")
    return stale


def load_answers(out: Path, name: str) -> list[dict[str, Any]]:
    """Answers for one arm, from `<name>.answers.jsonl`.

    Tolerates a code fence around the JSON. Models wrap objects in one about a third of the time
    however plainly asked not to, and discarding those runs would bias the sample toward the
    runs that happened to be tidy.
    """
    path = out / f"{name}.answers.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        match = re.search(r"\{.*\}", line, re.S)
        if match:
            rows.append(json.loads(match.group(0)))
    return rows


def tally(rows: list[dict[str, Any]], key: str = "choice") -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key, "?")
        counts[value] = counts.get(value, 0) + 1
    return counts


def spread(counts: dict[str, int]) -> str:
    return " ".join(
        f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )
