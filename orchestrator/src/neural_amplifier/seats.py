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

from collections.abc import Callable, Mapping

from .agent_brain import AgentBrain
from .brain import Brain, BrainError, ClaudeBrain, ScriptedBrain
from .config import Seat
from .contract import Orders, WorldView


def build_reflex(seat: Seat) -> Brain:
    """The driver for one seat, from the constructors ``build_brain`` already uses."""
    if seat.reflex == "scripted":
        return ScriptedBrain()
    if seat.reflex == "claude":
        return ClaudeBrain(model=seat.model) if seat.model else ClaudeBrain()
    if seat.reflex == "claude-code":
        from .claude_code_brain import ClaudeCodeBrain

        return ClaudeCodeBrain(model=seat.model)
    if seat.reflex == "agent":
        return AgentBrain()
    # `config.parse_seats` refuses every other reflex, so this is a Seat built by hand.
    raise ValueError(f"seat {seat.faction_id}: no driver for reflex {seat.reflex!r}")


class SeatRouter:
    """Routes each decision to the seat whose faction raised it."""

    name = "seats"

    def __init__(
        self,
        seats: Mapping[int, Seat],
        build: Callable[[Seat], Brain] = build_reflex,
    ) -> None:
        if not seats:
            raise ValueError("SeatRouter needs at least one seat; no [seats] means build_brain")
        self.seats: dict[int, Seat] = dict(seats)
        #: Built once, up front: a seat's driver keeps state across decisions (an agent's queue,
        #: a probed effort capability), and rebuilding it per call would discard that.
        self.drivers: dict[int, Brain] = {fid: build(seat) for fid, seat in self.seats.items()}

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
