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


@pytest.fixture(autouse=True, scope="session")
def _never_write_the_real_provider_event_log(tmp_path_factory: pytest.TempPathFactory):
    """Keep simulated provider outcomes out of the host's production counter journal.

    Claude-brain tests deliberately exercise transient, quota, and successful calls. Without
    this boundary those deterministic fixtures append to the same default JSONL as real games,
    making the monitoring rule page on a provider outage that never happened.

    Set both seams: the module constant protects this pytest process (which imports the module
    during collection), while the environment protects any child process a test launches.
    """
    from neural_amplifier import claude_code_brain

    event_log = tmp_path_factory.mktemp("provider-events") / "calls.jsonl"
    previous_env = os.environ.get("MODEL_PROVIDER_EVENT_LOG")
    previous_path = claude_code_brain.PROVIDER_EVENT_LOG
    os.environ["MODEL_PROVIDER_EVENT_LOG"] = str(event_log)
    claude_code_brain.PROVIDER_EVENT_LOG = event_log
    yield event_log
    claude_code_brain.PROVIDER_EVENT_LOG = previous_path
    if previous_env is None:
        os.environ.pop("MODEL_PROVIDER_EVENT_LOG", None)
    else:
        os.environ["MODEL_PROVIDER_EVENT_LOG"] = previous_env
