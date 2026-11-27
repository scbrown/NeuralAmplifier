"""The seat router: one brain per faction's chair (aegis-8fye.3, slice B).

``[seats.<faction_id>]`` names who sits in each chair (``config.Seat``). This module turns that
table into a :class:`~.brain.Brain` that routes every decision on ``world_view.faction_id`` to
the seat's own reflex, built with the same constructors ``build_brain`` uses for the
single-brain case — a seat is the existing brains, assigned, not a new kind of brain.

A decision the table cannot place raises :class:`~.brain.BrainError`: an unseated faction, or a
world view that does not say which faction it is. Both degrade to the deterministic tier through
the orchestrator's ordinary path. Routing an unplaceable decision to *some* seat instead would
put one faction's brain in another faction's chair, which is the fog boundary this whole table
exists to draw.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Mapping
from pathlib import Path

from .agent_brain import AgentBrain
from .brain import Brain, BrainError, ClaudeBrain, ScriptedBrain
from .config import Seat
from .contract import Orders, WorldView
from .pending import DecisionQueue

#: The header an MCP server sends its seat token in (slice C).
SEAT_HEADER = "X-NA-Seat"


def build_reflex(seat: Seat, queue: DecisionQueue | None = None) -> Brain:
    """The driver for one seat, from the constructors ``build_brain`` already uses."""
    if seat.reflex == "scripted":
        return ScriptedBrain()
    if seat.reflex == "claude":
        return ClaudeBrain(model=seat.model) if seat.model else ClaudeBrain()
    if seat.reflex == "claude-code":
        from .claude_code_brain import ClaudeCodeBrain

        return ClaudeCodeBrain(model=seat.model)
    if seat.reflex == "agent":
        # One queue for every agent seat, so the service mounts one set of /agent/* routes and
        # scopes each claim by seat rather than by which queue it happened to reach.
        return AgentBrain(queue=queue)
    # `config.parse_seats` refuses every other reflex, so this is a Seat built by hand.
    raise ValueError(f"seat {seat.faction_id}: no driver for reflex {seat.reflex!r}")


class SeatRouter:
    """Routes each decision to the seat whose faction raised it."""

    name = "seats"

    def __init__(
        self,
        seats: Mapping[int, Seat],
        build: Callable[[Seat], Brain] | None = None,
    ) -> None:
        if not seats:
            raise ValueError("SeatRouter needs at least one seat; no [seats] means build_brain")
        self.seats: dict[int, Seat] = dict(seats)
        if build is None:
            shared = DecisionQueue()

            def _shared(seat: Seat) -> Brain:
                return build_reflex(seat, queue=shared)

            build = _shared
        #: Built once, up front: a seat's driver keeps state across decisions (an agent's queue,
        #: a probed effort capability), and rebuilding it per call would discard that.
        self.drivers: dict[int, Brain] = {fid: build(seat) for fid, seat in self.seats.items()}
        agents = [d for d in self.drivers.values() if isinstance(d, AgentBrain)]
        queues = {id(a.queue) for a in agents}
        if len(queues) > 1:
            raise ValueError("every agent seat must share one DecisionQueue")
        #: The queue every agent seat parks decisions on, or None when no seat is an agent.
        self.agent_queue: DecisionQueue | None = agents[0].queue if agents else None

    def seat_for(self, world_view: WorldView) -> Seat | None:
        """The seat this decision belongs to, or None when the table cannot place it."""
        fid = world_view.faction_id
        return None if fid is None else self.seats.get(fid)

    def driver_for(self, world_view: WorldView) -> Brain | None:
        fid = world_view.faction_id
        return None if fid is None else self.drivers.get(fid)

    def decide(self, world_view: WorldView) -> Orders:
        fid = world_view.faction_id
        if fid is None:
            raise BrainError("seats: world view has no faction_id, so no seat can answer")
        driver = self.drivers.get(fid)
        if driver is None:
            seated = ", ".join(str(f) for f in sorted(self.drivers))
            raise BrainError(f"seats: faction {fid} is unseated (seated: {seated})")
        return driver.decide(world_view)

    def publish_outcome(self, outcome: dict[str, object]) -> None:
        """Hand the loop's outcome to whichever agent seat is driving this thread's decision.

        Each ``AgentBrain`` keeps its current decision thread-locally, so at most one of them
        has anything to publish; the others are silent, exactly as for an unseated run.
        """
        for driver in self.drivers.values():
            if isinstance(driver, AgentBrain):
                driver.publish_outcome(outcome)


class SeatTokens:
    """One bearer token per seat, minted at service start (aegis-8fye.3, slice C).

    The token is what makes a seat a seat on the agent side: the service derives the faction
    from it, so no agent-facing call carries a ``faction_id`` a model could get wrong or forge.
    Held only in memory here and written once per seat to ``<run>/seat-<n>/token`` at 0600 for
    that seat's MCP server to read; a restart mints new ones.
    """

    def __init__(self, seats: Mapping[int, Seat]) -> None:
        self._faction: dict[str, int] = {}
        self.tokens: dict[int, str] = {}
        for fid in sorted(seats):
            token = secrets.token_urlsafe(32)
            self.tokens[fid] = token
            self._faction[token] = fid

    def faction_for(self, token: str | None) -> int | None:
        """The seat a token belongs to, or None for a missing or unknown one."""
        if not token:
            return None
        for known, fid in self._faction.items():
            if secrets.compare_digest(known, token):
                return fid
        return None

    def write(self, run_dir: Path) -> list[Path]:
        """Write each seat's token to ``<run_dir>/seat-<n>/token``, mode 0600."""
        written: list[Path] = []
        for fid, token in self.tokens.items():
            seat_dir = run_dir / f"seat-{fid}"
            seat_dir.mkdir(parents=True, exist_ok=True)
            path = seat_dir / "token"
            # Created 0600 rather than chmod-ed after: there is no window where it is readable.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, token.encode())
            finally:
                os.close(fd)
            os.chmod(path, 0o600)
            written.append(path)
        return written


def _ids(rows: object, keys: tuple[str, ...]) -> set[int]:
    found: set[int] = set()
    if not isinstance(rows, list):
        return found
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = row.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                found.add(value)
    return found


def seat_entities(world_view: WorldView | None) -> tuple[set[int], set[int]]:
    """The unit ids and base ids a seat's latest world view shows it, as ``(units, bases)``.

    What a seated agent may order (slice C): an id its own fog-gated view never contained is
    another faction's unit, or one it is guessing at, and either way not its to command. Read
    from the engine-dependent ``units`` / ``bases`` sections and the decision's own ``base_id``.
    """
    if world_view is None:
        return set(), set()
    units = _ids(world_view.units, ("id", "unit_id", "veh_id"))
    for base in world_view.bases or []:
        garrison = base.get("garrison") if isinstance(base, dict) else None
        if isinstance(garrison, list):
            units |= {g for g in garrison if isinstance(g, int) and not isinstance(g, bool)}
    bases = _ids(world_view.bases, ("id", "base_id"))
    own = (world_view.model_extra or {}).get("base_id")
    if isinstance(own, int) and not isinstance(own, bool):
        bases.add(own)
    return units, bases
