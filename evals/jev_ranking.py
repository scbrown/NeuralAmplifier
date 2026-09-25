"""Pinned Jev scores for na-htm. Harvest is explicit; scoring never calls a model."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from neural_amplifier.jev import LEVELS, Caller, decision_state, from_env


def fingerprint(specs: dict[str, Any]) -> str:
    from multi_decision_ranking import _world_view

    payload = {name: {"state": decision_state(_world_view(spec, [])),
                      "grounding": spec["grounding"]} for name, spec in specs.items()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def harvest(path: Path, specs: dict[str, Any], caller: Caller) -> None:
    from multi_decision_ranking import _world_view

    results: dict[str, Any] = {}
    for name, spec in specs.items():
        results[name] = {}
        for fact in spec["grounding"]:
            state = decision_state(_world_view(spec, []))
            state["fact"] = fact
            result = caller.ask("score", state,
                                "How decision-relevant is this fact to this action space?", LEVELS)
            numeric_score(result)
            results[name][fact] = result
    pin = {"fingerprint": fingerprint(specs), "levels": LEVELS, "results": results}
    path.write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n")


def numeric_score(result: dict[str, Any]) -> float:
    value = result["answers"]["q"]["score"]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("missing Jev score")
    if not math.isfinite(value) or not 0 <= value <= len(LEVELS) - 1:
        raise ValueError("Jev score outside rubric")
    return float(value)


def load(path: Path, specs: dict[str, Any]) -> dict[str, list[str]]:
    pin = json.loads(path.read_text())
    if pin["fingerprint"] != fingerprint(specs) or pin["levels"] != LEVELS:
        raise ValueError("Jev pin is stale: harvest again before scoring")
    if set(pin["results"]) != set(specs):
        raise ValueError("Jev pin decision coverage differs")
    out = {}
    for name, spec in specs.items():
        results = pin["results"][name]
        if set(results) != set(spec["grounding"]):
            raise ValueError(f"Jev pin fact coverage differs for {name}")
        out[name] = sorted(spec["grounding"], key=lambda fact: -numeric_score(results[fact]))
    return out


def main() -> None:
    from multi_decision_ranking import PINNED, decisions

    caller = from_env()
    if caller is None:
        raise SystemExit("set NA_JEV_COMMAND to the camayoc Jev CLI argv")
    path = PINNED.with_name("jev-scores.json")
    if path.exists():
        raise SystemExit(f"{path} already exists; preserve it before harvesting a new run")
    harvest(path, decisions(), caller)
    print(f"Pinned Jev scores: {path}")


if __name__ == "__main__":
    main()
