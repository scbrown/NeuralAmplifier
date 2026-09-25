"""Optional, bounded adapter to camayoc's Jev CLI. No second API client.

Jev judgments are observations: routing never selects a brain, and the guard
never strips an order. Explicit configuration is required; tests inject a caller.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .contract import Orders, WorldView
from .knowledge import Ruling

LEVELS = [
    "No bearing on any offered action",
    "Background about the subject without differentiating the offered actions",
    "Explains a consequence of an offered action",
    "Distinguishes an important tradeoff between offered actions",
    "Establishes a decisive constraint on choosing among the offered actions",
]
TIERS = {
    "deterministic": "Mechanical choice with a clear local rule",
    "llm": "Strategic tradeoff needing reasoning",
    "bigger-model": "Complex, high-stakes tradeoff needing deeper reasoning",
    "none-of-these": "Insufficient information to recommend a tier",
}


class Caller(Protocol):
    def ask(
        self,
        kind: str,
        state: dict[str, Any],
        question: str,
        criteria: dict[str, str] | list[str] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CliCaller:
    command: tuple[str, ...]
    timeout: float = 0.5

    def ask(
        self,
        kind: str,
        state: dict[str, Any],
        question: str,
        criteria: dict[str, str] | list[str] | None = None,
    ) -> dict[str, Any]:
        args = [*self.command, kind, "--state", json.dumps(state), "--ask", question]
        if isinstance(criteria, dict):
            for key, value in criteria.items():
                if key != "none-of-these":
                    args.extend(["--option", f"{key}={value}"])
            args.extend(["--none", criteria["none-of-these"]])
        elif isinstance(criteria, list):
            for level in criteria:
                args.extend(["--level", level])
        started = time.monotonic()
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=self.timeout, check=False
        )
        if result.returncode:
            # Do not copy a provider error or subprocess stderr into game logs.
            raise RuntimeError(f"Jev CLI exited {result.returncode}")
        raw = json.loads(result.stdout)
        if not isinstance(raw, dict) or not isinstance(raw.get("answers", {}).get("q"), dict):
            raise ValueError("invalid Jev answer envelope")
        return {
            "request": {"state": state, "kind": kind, "question": question, "criteria": criteria},
            **raw,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }


def from_env() -> CliCaller | None:
    raw = os.environ.get("NA_JEV_COMMAND")
    if not raw:
        return None
    command = json.loads(raw)
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(arg, str) and arg for arg in command)
    ):
        raise ValueError("NA_JEV_COMMAND must be a nonempty JSON argv list")
    timeout = float(os.environ.get("NA_JEV_TIMEOUT", "0.5"))
    if not math.isfinite(timeout) or not 0 < timeout <= 2:
        raise ValueError("NA_JEV_TIMEOUT must be in (0, 2] seconds")
    return CliCaller(tuple(command), timeout)


def decision_state(view: WorldView) -> dict[str, Any]:
    """Only the decision menu and active intent; no history or hidden engine pick."""
    return {
        "surface": view.surface_id,
        "action_space": [a.model_dump(mode="json") for a in view.action_space],
        "directives": [d.model_dump(mode="json") for d in view.directives or []],
    }


def probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("missing numeric probability")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("probability outside [0, 1]")
    return float(value)


def tier_observation(caller: Caller, view: WorldView) -> dict[str, Any]:
    try:
        result = caller.ask(
            "choice",
            decision_state(view),
            "Which reasoning tier fits this decision? Do not choose an action.",
            TIERS,
        )
        answer = result["answers"]["q"]
        choice = answer["choice"]
        if choice not in TIERS:
            raise ValueError("unknown tier")
        confidence = probability(answer["confidence"])
        return {
            **result,
            "status": "observed",
            "acted_on": False,
            "suggestion": choice,
            "confidence": confidence,
        }
    except Exception as exc:  # advisory failures never stop a turn
        return {"status": "unavailable", "acted_on": False, "error": type(exc).__name__}


class JevGuard:
    def __init__(self, caller: Caller) -> None:
        self.caller = caller

    def rule(self, orders: Orders, world_view: WorldView) -> Ruling:
        if not world_view.directives or not orders.choices:
            return Ruling()
        state = decision_state(world_view)
        state["orders"] = [c.model_dump(mode="json") for c in orders.choices]
        try:
            result = self.caller.ask(
                "noul", state, "Do these proposed orders contradict any active directive?"
            )
            p = probability(result["answers"]["q"]["noul"])
            return Ruling(
                verdict="warn" if p >= 0.5 else "allow",
                advisories=("Jev advisory only: " + json.dumps(result, sort_keys=True),),
            )
        except Exception as exc:
            return Ruling(
                degraded=True,
                reason=f"Jev unavailable: {type(exc).__name__}",
                advisories=(f"Jev directive check unavailable: {type(exc).__name__}",),
            )
