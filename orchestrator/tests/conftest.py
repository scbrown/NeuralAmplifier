from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_amplifier.contract import WorldView

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> WorldView:
    return WorldView.model_validate(json.loads((FIXTURES / f"{name}.json").read_text()))


@pytest.fixture
def thinker_base() -> WorldView:
    """A rich Thinker world view: real fog, economy, and a fairness profile."""
    return load("thinker_base_production")


@pytest.fixture
def glsmac_thin() -> WorldView:
    """A thin GLSMAC world view: no economy, no fog, no end_turn action."""
    return load("glsmac_turn_thin")
