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
    if toml is None:
        return default
    # Narrowed rather than ignored. `toml` is whatever the file held, so int() on it can raise
    # TypeError as easily as ValueError — and the env branch above already turns a bad value
    # into a ConfigError naming it. A silent TypeError from the file branch would have been the
    # same mistake with worse wording.
    if isinstance(toml, int | float | str):
        try:
            return int(toml)
        except ValueError as exc:
            raise ConfigError(f"expected a whole number, got {toml!r}") from exc
    raise ConfigError(f"expected a whole number, got {toml!r}")


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
    quipu_dataset: str | None = None
    engine: str = "thinker"
    token_budget: int = 0
    guard: bool = True


@dataclass(frozen=True)
class Run:
    """Where a run's evidence goes. A record nobody keeps cannot be replayed or measured."""

    decision_log: str | None = None
    world_view_store: str | None = None
    #: Optional mutable companion to the standing-plan seed. When present, ``plan`` is loaded as
    #: immutable experiment input and all additions/revocations are persisted here instead.
    plan_state: str | None = None
    plan: str | None = None
    otel: bool = False
    #: The game's own directory — where `na-command` and `na-command-result` live. Required only
    #: to ISSUE orders (`/order`); every other path works without it, and absent means the order
    #: endpoint reports itself unavailable rather than the service refusing to start. Ordering is
    #: an addition to a run, not a precondition for one.
    #:
    #: It is a local path because the command channel is a FILE the adapter polls, so the
    #: orchestrator must share a filesystem with the game. Stated here because nothing else in
    #: this config implies co-location, and a remote orchestrator will silently order nothing.
    game_dir: str | None = None
    #: The run's own directory. Under a seat table each seat's token is written to
    #: ``<run_dir>/seat-<n>/token`` (aegis-8fye.3, slice C). Absent, the service infers it from a
    #: decision log at ``<run>/orch/...``.
    run_dir: str | None = None


#: Faction ids a seat can name. 0 is the native-life "faction" and never a player.
SEAT_IDS = range(1, 8)
SEAT_KEYS = {"principal", "strategist", "reflex", "model", "budget_usd"}
STRATEGISTS = {"agent", "human", "none"}
REFLEXES = {"scripted", "claude", "claude-code", "agent"}
#: Reflex kinds the design names but no slice has built yet. Refused rather than accepted as a
#: label that routes nothing, and the message names the slice that builds each (aegis-8fye.3).
UNBUILT_REFLEXES = {"skills": "aegis-8fye.5", "policy": "aegis-8fye.6", "copilot": "aegis-8fye.4"}


@dataclass(frozen=True)
class Seat:
    """Who sits in one faction's chair: an accountable principal, an optional strategist, and
    the reflex that answers each decision. One ``[seats.<faction_id>]`` table."""

    faction_id: int
    principal: str
    reflex: str
    strategist: str = "none"
    model: str | None = None
    budget_usd: float | None = None


def _seat_id(key: str) -> int:
    try:
        fid = int(key)
    except ValueError as exc:
        raise ConfigError(f"[seats.{key}]: a seat key is a faction id 1..7") from exc
    if fid not in SEAT_IDS:
        raise ConfigError(f"[seats.{key}]: faction id must be 1..7 (0 is native life)")
    return fid


def _seat(key: str, fid: int, body: object) -> Seat:
    where = f"[seats.{key}]"
    if not isinstance(body, dict):
        raise ConfigError(f"{where} must be a table")
    unknown = sorted(set(body) - SEAT_KEYS)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(SEAT_KEYS))}"
        )
    principal = body.get("principal")
    if not isinstance(principal, str) or not principal.strip():
        raise ConfigError(f"{where}: principal is required (a nonempty string)")
    reflex = body.get("reflex")
    if reflex is None:
        raise ConfigError(f"{where}: reflex is required ({'|'.join(sorted(REFLEXES))})")
    if reflex in UNBUILT_REFLEXES:
        raise ConfigError(
            f"{where}: reflex = {reflex!r} is not built yet; {UNBUILT_REFLEXES[reflex]} builds it"
        )
    if reflex not in REFLEXES:
        raise ConfigError(f"{where}: reflex must be one of {'|'.join(sorted(REFLEXES))}")
    strategist = body.get("strategist", "none")
    if strategist not in STRATEGISTS:
        raise ConfigError(f"{where}: strategist must be one of {'|'.join(sorted(STRATEGISTS))}")
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(f"{where}: model must be a string")
    budget = body.get("budget_usd")
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, int | float) or budget < 0:
            raise ConfigError(f"{where}: budget_usd must be a number >= 0")
        budget = float(budget)
    return Seat(fid, principal, reflex, strategist, model, budget)


def parse_seats(table: dict[str, Any]) -> dict[int, Seat]:
    """Build ``Config.seats`` from the ``[seats]`` table. Absent or empty means legacy: no seat
    table, and nothing anywhere changes."""
    seats: dict[int, Seat] = {}
    for key, body in table.items():
        fid = _seat_id(key)
        if fid in seats:
            raise ConfigError(f"[seats.{key}]: faction {fid} is seated twice")
        seats[fid] = _seat(key, fid, body)
    return seats


def llm_factions(seats: dict[int, Seat]) -> int:
    """The thinker.ini ``llm_factions`` bitmask the seat table implies: bit N = faction N."""
    return sum(1 << fid for fid in seats)


@dataclass(frozen=True)
class Config:
    """The whole configuration, already resolved against the environment."""

    brain: Brain = field(default_factory=Brain)
    knowledge: Knowledge = field(default_factory=Knowledge)
    run: Run = field(default_factory=Run)
    surfaces: SurfacePolicy = field(default_factory=SurfacePolicy)
    #: ``[seats.<faction_id>]``. Empty means legacy: no seat table was configured.
    seats: dict[int, Seat] = field(default_factory=dict)
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
            quipu_dataset=_text(env("NA_QUIPU_DATASET"), knowledge.get("quipu_dataset"), None),
            engine=_text(env("NA_ENGINE"), knowledge.get("engine"), "thinker") or "thinker",
            token_budget=_number(env("NA_TOKEN_BUDGET"), knowledge.get("token_budget"), 0),
            guard=_flag(env("NA_HANK_GUARD"), knowledge.get("guard"), True),
        ),
        run=Run(
            decision_log=_text(env("NA_DECISION_LOG"), run.get("decision_log"), None),
            world_view_store=_text(env("NA_WORLD_VIEW_STORE"), run.get("world_view_store"), None),
            plan_state=_text(env("NA_PLAN_STATE"), run.get("plan_state"), None),
            plan=_text(env("NA_PLAN"), run.get("plan"), None),
            otel=_flag(env("NA_OTEL"), run.get("otel"), False),
            game_dir=_text(env("NA_GAME_DIR"), run.get("game_dir"), None),
            run_dir=_text(env("NA_RUN_DIR"), run.get("run_dir"), None),
        ),
        surfaces=parse_surfaces(_table(data, "surfaces"), data.get("surface_default"), source),
        seats=parse_seats(_table(data, "seats")),
        source=source,
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m neural_amplifier.config llm-factions``: print the bitmask the seat table
    implies, or nothing when there is no ``[seats]`` table (legacy: leave thinker.ini alone).
    Exit 2 with the reason on a config that does not load."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if args != ["llm-factions"]:
        print("usage: python -m neural_amplifier.config llm-factions", file=sys.stderr)
        return 64
    try:
        cfg = load()
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2
    if cfg.seats:
        print(llm_factions(cfg.seats))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
