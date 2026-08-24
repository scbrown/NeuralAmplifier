from __future__ import annotations

import json
import os
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


@pytest.fixture(autouse=True, scope="session")
def _never_write_the_real_grounding_cache(tmp_path_factory: pytest.TempPathFactory):
    """Point Yupana's grounding cache at a throwaway directory for the whole session.

    Not tidiness. Grounding evidence is content-addressed and carries no marker saying which
    process wrote it, so a file a *fixture* produced is byte-identical in kind to one a real
    game produced — and Yupana, which is the component that decides whether an action was
    grounded, cannot tell them apart. A test able to reach the live cache is therefore a
    channel for manufacturing grounding for a real agent.

    This is not hypothetical: before ``Orchestrator``'s cache defaulted to ``None``, one run of
    this suite left two evidence files in ``~/.local/state/hank/grounding``. The default fixed
    the cause; this fixture removes the *reach*, so a future test that wires a cache by hand
    still cannot land in the real one.
    """
    root = tmp_path_factory.mktemp("grounding-cache")
    previous = os.environ.get("HANK_GROUNDING_CACHE_DIR")
    os.environ["HANK_GROUNDING_CACHE_DIR"] = str(root)
    yield root
    if previous is None:
        os.environ.pop("HANK_GROUNDING_CACHE_DIR", None)
    else:
        os.environ["HANK_GROUNDING_CACHE_DIR"] = previous
