"""Neural Amplifier orchestrator — the platform-agnostic LLM brain.

Speaks only ``docs/contract.md`` and never learns which engine it is driving.
"""

from __future__ import annotations

from .brain import Brain, BrainError, ClaudeBrain, ScriptedBrain
from .contract import Action, Choice, Fairness, Orders, WorldView
from .coverage import Report, report
from .decisions import DecisionLog, DecisionRecord
from .orchestrator import Orchestrator, Result
from .validate import validate

__all__ = [
    "Action",
    "Brain",
    "BrainError",
    "Choice",
    "ClaudeBrain",
    "DecisionLog",
    "DecisionRecord",
    "Fairness",
    "Orchestrator",
    "Orders",
    "Report",
    "Result",
    "ScriptedBrain",
    "WorldView",
    "report",
    "validate",
]

__version__ = "0.1.0"
