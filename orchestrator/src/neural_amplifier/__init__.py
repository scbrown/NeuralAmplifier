"""Neural Amplifier orchestrator — the platform-agnostic LLM brain.

Speaks only ``docs/contract.md`` and never learns which engine it is driving.
"""

from __future__ import annotations

from .brain import Brain, BrainError, ClaudeBrain, ScriptedBrain
from .contract import Action, Choice, Fairness, Orders, WorldView
from .coverage import Report, report
from .decisions import DecisionLog, DecisionRecord
from .fairness import fairness_profile, handicap_drift
from .fog import redact
from .knowledge import Grounding, Guard, Retriever, Ruling
from .orchestrator import Orchestrator, Result
from .replay import Comparison, WorldViewStore, replay
from .telemetry import Emitter, OtelSink, Sink, sinks_for
from .validate import validate

__all__ = [
    "Action",
    "Brain",
    "BrainError",
    "Choice",
    "Comparison",
    "ClaudeBrain",
    "DecisionLog",
    "DecisionRecord",
    "Emitter",
    "Fairness",
    "Grounding",
    "Guard",
    "Orchestrator",
    "Orders",
    "OtelSink",
    "Report",
    "Result",
    "Retriever",
    "Ruling",
    "ScriptedBrain",
    "Sink",
    "WorldView",
    "WorldViewStore",
    "fairness_profile",
    "handicap_drift",
    "redact",
    "replay",
    "report",
    "sinks_for",
    "validate",
]

__version__ = "0.1.0"
