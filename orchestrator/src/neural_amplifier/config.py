"""``na.toml`` — one config file for the whole orchestrator.

Everything that was a scattered ``NA_*`` environment read is a key here: which brain, where
Quipu is, what gets logged, and which surfaces the LLM tier owns. One file so a run is
reproducible from something you can read and commit, rather than from ten variables that have to
be right at once and leave no trace in the record when they are not.

**Environment variables still win.** Precedence is env > file > default, and that ordering is
deliberate rather than incidental: the file is what a run *is*, and the variable is how you
override one thing for one run without editing the tree — which is exactly what CI and the cloud
setup script do. Reversing it would mean a checked-in file silently overriding the harness.

**A malformed file refuses to start.** It is read once, at service construction, so a typo fails
the process rather than one turn at a time in a running game — and an unknown surface id is an
error rather than a toggle that appears set and does nothing.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .policy import SurfacePolicy, parse_surfaces

#: Repo root, beside the justfile. Overridable with ``NA_CONFIG``.
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "na.toml"

#: Values a person would reasonably write as on/off. `bool()` on a string is a trap: every
#: non-empty string is truthy, so `NA_HANK_GUARD=0` would have *enabled* the guard.
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """A config that cannot be trusted. Raised at load, never at decision time."""


def _flag(env: str | None, toml: object, default: bool) -> bool:
    if env is not None:
        return env.strip().lower() not in _FALSE
    return bool(toml) if toml is not None else default


def _text(env: str | None, toml: object, default: str | None) -> str | None:
    if env:
        return env
    return str(toml) if toml is not None else default


def _number(env: str | None, toml: object, default: int) -> int:
    if env:
        try:
            return int(env)
        except ValueError as exc:
            raise ConfigError(f"expected a whole number, got {env!r}") from exc
    return int(toml) if toml is not None else default  # type: ignore[arg-type]


@dataclass(frozen=True)
class Brain:
    """Which brain answers, and as what.

    ``kind`` defaults to ``scripted`` and the real brain is opt-in, because CI must never make
    a paid call by accident — an accident that costs money and, worse, makes a test suite's
    result depend on a model.
    """

    kind: str = "scripted"
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class Knowledge:
    """The Quipu/Hank seam. All optional: absent means a less-informed decision, never a
    stalled turn."""

    quipu_url: str | None = None
    engine: str = "thinker"
    token_budget: int = 0
    guard: bool = True


@dataclass(frozen=True)
class Run:
    """Where a run's evidence goes. A record nobody keeps cannot be replayed or measured."""

    decision_log: str | None = None
    world_view_store: str | None = None
    plan: str | None = None
    otel: bool = False


@dataclass(frozen=True)
class Config:
    """The whole configuration, already resolved against the environment."""

    brain: Brain = field(default_factory=Brain)
    knowledge: Knowledge = field(default_factory=Knowledge)
    run: Run = field(default_factory=Run)
    surfaces: SurfacePolicy = field(default_factory=SurfacePolicy)
    #: None when no file was found. Distinguishes "nothing configured" from "configured empty",
    #: which the surface policy in particular has to tell apart.
    source: Path | None = None


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] must be a table")
    return section


def load(path: Path | None = None) -> Config:
    """Read ``na.toml`` and resolve it against the environment.

    A missing file is not an error — it means nothing has been configured, and every default
    here is the behaviour this had before the file existed.
    """
    if path is None:
        override = os.environ.get("NA_CONFIG")
        path = Path(override) if override else DEFAULT_PATH

    data: dict[str, Any] = {}
    source: Path | None = None
    if path.exists():
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        source = path

    env = os.environ.get
    brain = _table(data, "brain")
    knowledge = _table(data, "knowledge")
    run = _table(data, "run")

    return Config(
        brain=Brain(
            kind=(_text(env("NA_BRAIN"), brain.get("kind"), "scripted") or "scripted").lower(),
            model=_text(env("NA_BRAIN_MODEL"), brain.get("model"), None),
            effort=_text(env("NA_BRAIN_EFFORT"), brain.get("effort"), None),
        ),
        knowledge=Knowledge(
            quipu_url=_text(env("NA_QUIPU_URL"), knowledge.get("quipu_url"), None),
            engine=_text(env("NA_ENGINE"), knowledge.get("engine"), "thinker") or "thinker",
            token_budget=_number(env("NA_TOKEN_BUDGET"), knowledge.get("token_budget"), 0),
            guard=_flag(env("NA_HANK_GUARD"), knowledge.get("guard"), True),
        ),
        run=Run(
            decision_log=_text(env("NA_DECISION_LOG"), run.get("decision_log"), None),
            world_view_store=_text(env("NA_WORLD_VIEW_STORE"), run.get("world_view_store"), None),
            plan=_text(env("NA_PLAN"), run.get("plan"), None),
            otel=_flag(env("NA_OTEL"), run.get("otel"), False),
        ),
        surfaces=parse_surfaces(_table(data, "surfaces"), data.get("surface_default"), source),
        source=source,
    )
