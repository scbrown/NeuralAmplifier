"""What the ENGINE did with an order, as distinct from what the orchestrator decided.

Three different things have been called an "outcome" in this project and only two of them
existed until now:

1. **What the brain chose.** In the decision record.
2. **What the orchestrator applied.** `publish_outcome` on the agent brain — validation and the
   policy guard sit between a choice and what is returned, so an agent told its *stripped*
   choice was applied has no reason to repair it.
3. **What the engine did.** This module. Nothing carried it.

The third is not a refinement of the second. An order can pass every gate the orchestrator has,
be returned to the adapter, be accepted by the engine's own availability tests — and still not be
what the base builds, because a later engine path overwrote the queue or a rule nobody encoded
intervened. The adapter already detects that (`na_verify_base_production` reads the state back
after the apply and emits a divergence event) and wrote it only to a local file the orchestrator
never read. So the orchestrator's picture of its own effect was *absent*, not merely incomplete.

**Correlation is by `traceparent`, not by a decision id.** Decision ids belong to the agent
queue and exist only when an `AgentBrain` is mounted; the traceparent is stamped by the adapter
in `na_write_head` on *every* world view, on every surface, for every brain. It is already a
correlation key by construction, which is what W3C trace context is for. Using it means outcome
reporting works for scripted and model brains too, and needs no new identifier to keep in sync.

**Silence is not success.** An order whose outcome never arrives is reported as `unknown` and
never as applied. That is the whole discipline of this module: the game can crash, the adapter
can be a build behind, the socket can drop — and every one of those looks exactly like "it
worked" if the absence of a report is read as confirmation.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: How many decisions' outcomes to keep. A long game is tens of thousands of decisions and
#: nothing here is durable — the decision log is the durable record. This is a live window for
#: an agent to ask "did what I just ordered actually happen", so it wants to be big enough to
#: span a turn's worth of bases and no bigger.
DEFAULT_CAPACITY = 512


class EngineOutcome(BaseModel):
    """One report from the adapter about what the engine did.

    ``extra="allow"`` deliberately, matching the rest of the contract: the adapter emits JSON by
    hand from C++ with ``snprintf`` and a field we have not modelled yet must not 422 the report.
    Losing an outcome to a schema disagreement would restore exactly the blindness this exists to
    remove.
    """

    model_config = ConfigDict(extra="allow")

    #: The W3C traceparent from the world view this outcome belongs to. The correlation key.
    traceparent: str
    #: Which game process. A report from a run that is gone is not about this game.
    run_id: str | None = None
    surface_id: str | None = None
    turn: int | None = None
    base_id: int | None = None
    base: str | None = None

    #: ``applied`` — the ordinary report, written when the decision was applied.
    #: ``divergence`` — the engine did not keep it; a genuinely second event, which is why the
    #: adapter writes it as its own record rather than amending the first.
    event: Literal["applied", "divergence"] = "applied"

    #: ``deterministic`` | ``llm``. What actually decided it, per the adapter.
    tier: str | None = None
    #: ``native`` | ``llm`` | ``llm_degraded``.
    applied: str | None = None
    applied_item: int | None = None
    applied_item_name: str | None = None
    #: On divergence: what we asked for, against ``applied_*`` which is what the engine has.
    intended_item: int | None = None
    intended_item_name: str | None = None
    #: On a replay whose cached choice became illegal.
    superseded_item: int | None = None
    #: Why it is not what was decided. Never invented — the adapter declines to name a cause it
    #: has not established, and so does this field.
    fallback_reason: str | None = None


class OutcomeReport(BaseModel):
    """What an agent gets back when it asks about a decision it answered."""

    traceparent: str
    #: ``unknown`` until the adapter reports. Never defaults to anything hopeful.
    status: Literal["unknown", "applied", "diverged"] = "unknown"
    outcomes: list[EngineOutcome] = Field(default_factory=list)


class OutcomeStore:
    """Recent engine outcomes, keyed by traceparent.

    A single decision can produce more than one: the apply, and then a divergence discovered
    afterwards. Both are kept, in arrival order, because collapsing them would lose the fact that
    something *undid* a decision that had already succeeded — which is the only interesting case.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._by_trace: OrderedDict[str, list[EngineOutcome]] = OrderedDict()
        #: Monotonic, so an agent can ask "anything since I last looked" without a clock. A
        #: timestamp would invite comparing it against the game's turn, which is a different
        #: axis entirely.
        self._seq = 0
        self._order: list[tuple[int, str]] = []
        self._lock = threading.Lock()

    def record(self, outcome: EngineOutcome) -> int:
        with self._lock:
            self._seq += 1
            bucket = self._by_trace.get(outcome.traceparent)
            if bucket is None:
                bucket = []
                self._by_trace[outcome.traceparent] = bucket
            bucket.append(outcome)
            self._by_trace.move_to_end(outcome.traceparent)
            self._order.append((self._seq, outcome.traceparent))
            while len(self._by_trace) > self._capacity:
                self._by_trace.popitem(last=False)
            return self._seq

    def get(self, traceparent: str) -> OutcomeReport:
        """What is known about one decision.

        An absent traceparent is ``unknown``, which is deliberately not an error: the adapter may
        simply not have reported yet, and a 404 would push a caller toward treating "no answer"
        as "no problem".
        """
        with self._lock:
            found = list(self._by_trace.get(traceparent, ()))
        return OutcomeReport(
            traceparent=traceparent,
            status=_status(found),
            outcomes=found,
        )

    def since(self, cursor: int = 0, limit: int = 100) -> tuple[int, list[EngineOutcome]]:
        """Outcomes recorded after ``cursor``, and the new cursor.

        The cursor is returned even when the list is empty so a polling agent advances rather
        than re-reading the same tail forever.
        """
        with self._lock:
            fresh: list[EngineOutcome] = []
            high = cursor
            for seq, trace in self._order:
                if seq <= cursor:
                    continue
                bucket = self._by_trace.get(trace)
                if not bucket:
                    # Evicted by capacity. Its sequence still advances the cursor: pretending it
                    # is still pending would leave a poller waiting for something that can never
                    # arrive.
                    high = max(high, seq)
                    continue
                fresh.append(bucket[-1])
                high = max(high, seq)
                if len(fresh) >= limit:
                    break
            # Trim what we can no longer serve, so `_order` does not grow without bound over a
            # long game.
            if len(self._order) > self._capacity * 4:
                keep = {t for _, t in self._order[-self._capacity :]}
                self._order = [(s, t) for s, t in self._order if t in keep]
        return high, fresh

    def stats(self) -> dict[str, int]:
        with self._lock:
            applied = diverged = 0
            for bucket in self._by_trace.values():
                status = _status(bucket)
                if status == "applied":
                    applied += 1
                elif status == "diverged":
                    diverged += 1
            return {
                "decisions": len(self._by_trace),
                "applied": applied,
                "diverged": diverged,
                "reports": self._seq,
            }


def _status(outcomes: list[EngineOutcome]) -> Literal["unknown", "applied", "diverged"]:
    """Divergence wins, whatever order the reports arrived in.

    A divergence always follows an apply — the decision happened, then something undid it — so
    reading the last report would be right today and wrong the first time two reports race. The
    question an agent is asking is "is what I ordered what the game has", and any divergence
    answers no.
    """
    if not outcomes:
        return "unknown"
    if any(o.event == "divergence" for o in outcomes):
        return "diverged"
    return "applied"
