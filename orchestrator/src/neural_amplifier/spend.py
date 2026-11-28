"""The LLM spend ceiling: refuse paid brain calls once a game has spent its budget.

``budget_usd`` used to be display-only — the dashboard showed it and nothing stopped a call.
This is the guard in front of every paid call.

**What is counted is the decision log.** Every decision record carries the ``cost_usd`` its
brain call was billed for, and the ledger is the sum of those records per ``game_id`` (and per
seat under a seat table). It is fed two ways that are the same thing: seeded from the log
already on disk when the service starts, then kept current as a telemetry sink receiving each
record the moment it is written. So the number that trips the ceiling is the number the
dashboard shows, never a second in-memory counter that could disagree with it.

**Where the budget comes from.**

- Seat mode (``[seats.<n>]``): that seat's ``budget_usd``. Each seat is metered on its own, so
  one seat over its budget never stops another.
- Legacy unseated mode: ``NA_BUDGET_USD``, else ``[run] budget_usd`` in ``na.toml``.
- Unset means no ceiling — exactly the behaviour before this existed.

**What happens when it trips.** The brain is not asked. The decision is recorded at
``tier="fallback"``, ``degraded=true``, with a ``degrade_reason`` starting ``spend-ceiling``,
and the engine applies its own native answer (empty orders). One WARNING line is logged the
first time each game/seat trips; the reason is on every later record, so it is never silent.

**Pricing the API lane.** ``claude -p`` reports a billed ``total_cost_usd``. The Anthropic API
reports tokens only, so ``ClaudeBrain`` prices its own usage with :func:`price_usage`: the
published per-million-token rates below, cache reads at 0.1x and cache writes at 1.25x the input
rate. A model missing from the table is priced at the HIGHEST listed rate, so an unknown model
trips the ceiling early rather than never. ``NA_BRAIN_PRICE="<input>,<output>"`` (USD per million
tokens) overrides the table for a contract or partner rate.

**Not atomic.** The check runs before a call and the spend lands when its record is written, so
decisions in flight at the same moment can each pass the check: the ceiling can be overshot by up
to one call per concurrent decision. It bounds spend; it is not an exact cap.

Only brains that declare ``paid = True`` are guarded. An agent, human or scripted seat costs
nothing per call here, and degrading one because a number reached zero would stop a player who
spends nothing.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterable, Mapping

from .decisions import DecisionRecord

log = logging.getLogger(__name__)

#: Prefix of every ``degrade_reason`` this guard writes. Stable: the dashboard and any log
#: reader recognise a spend-ceiling degradation by it.
SPEND_CEILING = "spend-ceiling"


#: Anthropic first-party rates, USD per million tokens (input, output). Matched by prefix, longest
#: first, so ``claude-haiku-4-5-20251001`` prices as ``claude-haiku-4-5``.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}
CACHE_READ = 0.1
CACHE_WRITE = 1.25


def _rates(model: str) -> tuple[float, float]:
    override = os.environ.get("NA_BRAIN_PRICE")
    if override:
        try:
            inp, out = (float(x) for x in override.split(","))
            return inp, out
        except ValueError:
            log.warning("NA_BRAIN_PRICE=%r is not '<input>,<output>'; using the table", override)
    for prefix in sorted(PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICES[prefix]
    # Unknown model: the dearest listed rate, so the ceiling trips early rather than never.
    return max(PRICES.values())


def price_usage(model: str, usage: Mapping[str, int] | None) -> float | None:
    """USD for one API call from its reported token usage; None when no usage was reported."""
    if not usage:
        return None
    inp, out = _rates(model)
    total = (
        usage.get("input", 0) * inp
        + usage.get("cached", 0) * inp * CACHE_READ
        + usage.get("cache_write", 0) * inp * CACHE_WRITE
        + usage.get("output", 0) * out
    )
    return total / 1_000_000


def resume_game_id(
    records: Iterable[DecisionRecord], ledger: SpendLedger | None = None
) -> str | None:
    """The ``game_id`` of the newest record in a decision log, seeding ``ledger`` on the way.

    One pass over the log: a restarted service continues the game its log belongs to, so the
    meter it seeds and the id it checks the ceiling against are the same game.
    """
    last: str | None = None
    for record in records:
        if ledger is not None:
            ledger.write(record)
        if record.game_id:
            last = record.game_id
    return last


def record_cost(record: DecisionRecord) -> float | None:
    """Model spend in USD when the producer recorded it; ``None`` when it did not.

    Reads the declared field first, then the legacy ``usd`` extra older logs may carry.
    """
    if record.cost_usd is not None:
        return float(record.cost_usd)
    extra = record.model_extra or {}
    raw = extra.get("usd")
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


class SpendLedger:
    """Per-game, per-seat spend summed from decision records, with the ceiling check."""

    def __init__(
        self,
        default_budget: float | None = None,
        seat_budgets: Mapping[int, float | None] | None = None,
    ) -> None:
        #: The legacy (unseated) ceiling. Ignored under a seat table: there each seat's own
        #: ``budget_usd`` is the ceiling, and an unset one means that seat has none.
        self.default_budget = default_budget
        self.seat_budgets: dict[int, float | None] = dict(seat_budgets or {})
        self._spent: dict[tuple[str, int | None], float] = {}
        self._tripped: set[tuple[str, int | None]] = set()
        self._lock = threading.Lock()

    @property
    def seated(self) -> bool:
        return bool(self.seat_budgets)

    def budget_for(self, seat: int | None) -> float | None:
        if self.seated:
            return self.seat_budgets.get(seat) if seat is not None else None
        return self.default_budget

    def _key(self, game_id: str, seat: int | None) -> tuple[str, int | None]:
        # Unseated mode meters the whole game as one; a seat id on a record there is ignored.
        return (game_id, seat if self.seated else None)

    def spent(self, game_id: str, seat: int | None = None) -> float:
        with self._lock:
            return self._spent.get(self._key(game_id, seat), 0.0)

    # ------------------------------------------------------------------ feeding

    def write(self, record: DecisionRecord) -> None:
        """The Sink half: every record the log receives is added to its game's total."""
        cost = record_cost(record)
        if cost is None or cost <= 0:
            return
        key = self._key(record.game_id or "", record.seat)
        with self._lock:
            self._spent[key] = self._spent.get(key, 0.0) + cost

    def load(self, records: Iterable[DecisionRecord]) -> SpendLedger:
        """Seed from a decision log already on disk, so a restart does not reset the meter."""
        for record in records:
            self.write(record)
        return self

    # ------------------------------------------------------------------ the guard

    def refusal(self, game_id: str, seat: int | None = None, pending: float = 0.0) -> str | None:
        """The ``degrade_reason`` when this game/seat may not make another paid call, else None.

        ``pending`` is what the decision in flight has already been billed (an earlier repair
        attempt), which the log cannot hold yet because its record is written at the end.
        """
        budget = self.budget_for(seat)
        if budget is None:
            return None
        key = self._key(game_id, seat)
        with self._lock:
            spent = self._spent.get(key, 0.0) + pending
            if spent < budget:
                return None
            first = key not in self._tripped
            self._tripped.add(key)
        who = f"seat {seat}" if self.seated else "game"
        reason = (
            f"{SPEND_CEILING}: {who} spent ${spent:.4f} of its ${budget:.4f} budget; "
            "paid brain calls refused, native answer applied"
        )
        if first:
            log.warning("%s (game_id=%s)", reason, game_id)
        return reason

    def status(self) -> list[dict[str, object]]:
        """Every metered game/seat with its spend, budget and whether the ceiling tripped."""
        with self._lock:
            keys = sorted({*self._spent, *self._tripped}, key=lambda k: (k[0], k[1] if k[1] else 0))
            return [
                {
                    "game_id": game,
                    "seat": seat,
                    "spent_usd": round(self._spent.get((game, seat), 0.0), 6),
                    "budget_usd": self.budget_for(seat),
                    "tripped": (game, seat) in self._tripped,
                }
                for game, seat in keys
            ]
