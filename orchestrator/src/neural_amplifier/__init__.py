"""Neural Amplifier orchestrator — the platform-agnostic LLM brain.

Speaks only ``docs/contract.md`` and never learns which engine it is driving.
"""

from __future__ import annotations

from .brain import Brain, BrainError, ClaudeBrain, ScriptedBrain
from .contract import Action, Choice, Fairness, Orders, WorldView
from .coverage import Report, report
from .decisions import DecisionLog, DecisionRecord
from .fairness import fairness_profile, handicap_drift
from .orchestrator import Orchestrator, Result
from .telemetry import Emitter, OtelSink, Sink, sinks_for
from .validate import validate

__all__ = [
    "Action",
    "Brain",
    "BrainError",
    "Choice",
    "ClaudeBrain",
    "DecisionLog",
    "DecisionRecord",
    "Emitter",
    "Fairness",
    "Orchestrator",
    "Orders",
    "OtelSink",
    "Report",
    "Result",
    "ScriptedBrain",
    "Sink",
    "WorldView",
    "fairness_profile",
    "handicap_drift",
    "report",
    "sinks_for",
    "validate",
]

__version__ = "0.1.0"
