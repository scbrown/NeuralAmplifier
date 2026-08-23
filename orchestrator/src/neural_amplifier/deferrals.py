"""Decisions the agent declined to answer *now* and intends to answer properly.

Door 1 cannot wait. `mod_base_build` takes one int and returns one int on the engine's own
thread, and the adapter's comment says it plainly: *"nowhere to park a decision and resume it
later"* (`neural.cpp`). So "ask me later" cannot be a return value.

It can be a *record*. Three things already built make that work, and none of them are new here:

1. Every `/decide` failure path already applies the engine's own pick. Empty orders mean "we
   name no action", and the adapter answers itself — so **"no answer" is already a safe
   provisional answer**, and has been since invariant 9.
2. `base.production` is a revisable queue write, last-write-wins, and the engine re-asks bases
   within a turn (`docs/turn-scoped-play.md` §3, §6).
3. Door 2's `build` verb sets a base's item directly, out of band, whenever we like.

A deferral is what connects them: the engine gets an answer immediately and keeps playing, the
decision stays OPEN here, and the agent resolves it at its own pace through door 2.

**A deferral is not a degradation, and the tier is where that distinction lives.** Both apply
the engine's choice; they mean opposite things. `deterministic` says nobody was asked or nobody
answered. `deferred` says the agent read the world view, decided it wanted more than the
engine's clock allows, and took responsibility for coming back. Recording the second as the
first would make a working mechanism indistinguishable from a broken brain in every metric we
have — `degrade_rate` most of all, which exists to catch a run where the brain was silently
absent.

**Expiry is honest, not tidy.** A deferral nobody resolves before the turn moves on is closed
`expired`, which records the truth: the engine's choice stood, and it stood because we did not
come back. It is not an error and it is not a success, and it must not be recorded as either.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

#: The action id an agent returns to defer. Not a member of any action space — the engine never
#: offers it — so it is recognised BEFORE validation, which would otherwise correctly report it
#: as an id the engine did not offer and throw the decision into the degrade path. That ordering
#: is the whole implementation on the orchestrator side.
DEFER_ACTION_ID = "defer"

Status = Literal["open", "resolved", "expired"]


def is_defer(action_id: str | None) -> bool:
    """Case-insensitively, and tolerant of surrounding space.

    Deliberately forgiving where the rest of the action-space contract is exact. An action id is
    matched against a list the engine supplied, so a near-miss is caught and reported; `defer` is
    matched against nothing, so a near-miss would be silently validated away as an unknown id and
    the agent would be told its answer was "not applied" with no hint that it nearly worked.
    """
    if action_id is None:
        return False
    return action_id.strip().lower() == DEFER_ACTION_ID


@dataclass
class Deferral:
    """One decision the agent parked, and everything needed to come back to it."""

    id: str
    surface_id: str
    turn: int | None = None
    faction_id: int | None = None
    faction: str | None = None
    base_id: int | None = None
    base: str | None = None
    #: What the engine applied in the meantime. Recorded because it is the thing a resolution
    #: REPLACES, and because when a deferral expires this is the answer that stood.
    standing_action_id: str | None = None
    standing_action: str | None = None
    #: The grounded view the agent read — the same bytes the brain was given, so resolving does
    #: not mean re-deriving the situation from memory.
    world_view: dict[str, Any] = field(default_factory=dict)
    status: Status = "open"
    #: Free text on a closed deferral: the order that resolved it, or why it expired.
    resolution: str | None = None
    reason: str | None = None

    def summary(self) -> dict[str, Any]:
        """The listing shape, without the world view."""
        return {
            "id": self.id,
            "surface_id": self.surface_id,
            "turn": self.turn,
            "faction_id": self.faction_id,
            "faction": self.faction,
            "base_id": self.base_id,
            "base": self.base,
            "standing_action_id": self.standing_action_id,
            "standing_action": self.standing_action,
            "status": self.status,
            "resolution": self.resolution,
            "reason": self.reason,
        }


class DeferralSet:
    """The open deferrals, oldest first.

    Locked like `DecisionQueue`, and for the same reason: `/decide` runs on the game's blocked
    worker thread while the agent reads and resolves on request threads.
    """

    def __init__(self, max_open: int = 256) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Deferral] = {}
        self._max_open = max_open

    # -- writes ---------------------------------------------------------------

    def open(self, deferral: Deferral) -> Deferral:
        """Park a decision. Re-deferring the same id REPLACES the entry.

        The engine asks a base several times per turn, so the same decision genuinely can be
        deferred twice; keeping both would show the agent two entries for one base and invite it
        to answer the same thing twice through door 2.
        """
        with self._lock:
            self._items[deferral.id] = deferral
            self._evict()
            return deferral

    def resolve(self, deferral_id: str, resolution: str) -> Deferral | None:
        """Close one deferral because the agent acted on it. `None` if there is no open one."""
        with self._lock:
            item = self._items.get(deferral_id)
            if item is None or item.status != "open":
                return None
            item.status = "resolved"
            item.resolution = resolution
            return item

    def resolve_for_base(
        self, base_id: int, resolution: str, faction_id: int | None = None
    ) -> list[Deferral]:
        """Close whatever the agent just answered through door 2.

        The agent resolves a build deferral by issuing `build <base_id> <item>` — the verb that
        already exists — rather than by calling a second endpoint that means the same thing. So
        the link back to the deferral has to be made from the order's own arguments, and
        `base_id` is what both sides have.

        Faction is checked when the caller knows it. It usually does not: `POST /order` carries a
        base id and an item id and nothing else, which is the engine's own grammar.
        """
        closed: list[Deferral] = []
        with self._lock:
            for item in self._items.values():
                if item.status != "open" or item.base_id != base_id:
                    continue
                if faction_id is not None and item.faction_id != faction_id:
                    continue
                item.status = "resolved"
                item.resolution = resolution
                closed.append(item)
        return closed

    def expire_before(self, turn: int) -> list[Deferral]:
        """Close every open deferral raised before `turn`, and say what that means.

        Called when a turn advances. The engine's answer has been played by then and a base's
        minerals have moved, so a resolution arriving now would apply to the NEXT turn's build —
        which is a different decision, not a late answer to this one.
        """
        expired: list[Deferral] = []
        with self._lock:
            for item in self._items.values():
                if item.status != "open" or item.turn is None or item.turn >= turn:
                    continue
                item.status = "expired"
                item.reason = (
                    f"unresolved when turn {turn} began; the engine's own choice"
                    f"{f' ({item.standing_action})' if item.standing_action else ''} stood"
                )
                expired.append(item)
        return expired

    def _evict(self) -> None:
        """Bound the set. Caller holds the lock.

        Drops CLOSED entries first and only then the oldest open one — a closed deferral is
        history and an open one is outstanding work, so losing the second to keep the first is
        backwards. Insertion order is dict order, so oldest is first.
        """
        if len(self._items) <= self._max_open:
            return
        for key, item in list(self._items.items()):
            if len(self._items) <= self._max_open:
                return
            if item.status != "open":
                del self._items[key]
        while len(self._items) > self._max_open:
            del self._items[next(iter(self._items))]

    # -- reads ----------------------------------------------------------------

    def pending(self, faction_id: int | None = None) -> list[Deferral]:
        """The open deferrals, scoped to one faction when asked.

        A parked decision is faction-private state — it names a base, and it carries the
        grounded world view the agent read when it parked it. So the same boundary that scopes
        recall and the turn view scopes this, by the same rule and for the same reason.

        A deferral with no attributable faction is withheld, not shown: `resolve_for_base`
        already refuses to match one against a stated faction, and a read that were laxer than
        the write would hand out exactly the rows the write will not let you act on.

        Unscoped stays the observer's read — `/agent/pending` mounts whatever the brain is, and
        an operator asking what is parked across a six-faction round is asking a legitimate
        question about the run rather than making a decision inside it.
        """
        with self._lock:
            open_items = [i for i in self._items.values() if i.status == "open"]
        if faction_id is None:
            return open_items
        return [i for i in open_items if i.faction_id == faction_id]

    def get(self, deferral_id: str) -> Deferral | None:
        with self._lock:
            return self._items.get(deferral_id)

    def all(self) -> Iterable[Deferral]:
        with self._lock:
            return list(self._items.values())
