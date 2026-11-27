"""The console's live stream — ``docs/seats-and-policy.md`` §7 (aegis-8fye.4, slice 1).

:class:`LiveStream` is one more :class:`~.telemetry.Sink`. It is registered *after* the JSONL
record of truth (``telemetry.sinks_for``), so an event a viewer sees has already been written
to the log: the stream can lag the record, but it can never say something the record does not.

It also carries the two lifecycle steps that happen before a record exists — a decision being
**raised** and **claimed** — and the engine's **applied** / **diverged** report afterwards,
because a record is assembled only once the decision is over, and the Now pane is about the
part before that.

Two rules make it safe to hang off ``emit()``:

* **A viewer never blocks the game.** Each subscriber has a bounded queue. Publishing appends
  and returns; a full queue drops its *oldest* event and counts the drop. Nothing here waits on
  a reader, and nothing here raises into the emitter.
* **Filtering happens here, not in the browser.** A subscription is opened for one faction (a
  seat's player view) or for all of them (the spectator view), and an event for another faction
  is never put on that subscriber's queue. Sending everything and hiding it client-side would
  put the other seat's turn on the wire.
"""

from __future__ import annotations

import collections
import itertools
import json
import os
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contract import WorldView
from .decisions import DecisionRecord

#: Per-viewer backlog. A console that stops reading for this many events starts losing the
#: oldest ones, which is the right thing to lose: the Now pane is about the present.
DEFAULT_QUEUE = 256

#: The run-record file ``observer_spoiled`` is written to, under the run directory.
RUN_RECORD = "run.json"


def sse_frame(event: str, data: Mapping[str, Any], event_id: int | None = None) -> str:
    """One Server-Sent Events frame. ``data`` is JSON on a single line, so it cannot break framing."""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n\n"


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse frames back into ``{"id", "event", "data"}`` dicts; comments (heartbeats) are skipped."""
    out: list[dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        frame: dict[str, Any] = {}
        data: list[str] = []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "data":
                data.append(value)
            elif key == "id":
                frame["id"] = int(value)
            elif key == "event":
                frame["event"] = value
        if data:
            frame["data"] = json.loads("\n".join(data))
            frame.setdefault("event", "message")
            out.append(frame)
    return out


@dataclass
class Subscription:
    """One viewer's bounded queue. ``faction`` None is the spectator view."""

    faction: int | None
    maxlen: int = DEFAULT_QUEUE
    dropped: int = 0
    closed: bool = False
    _events: collections.deque[tuple[int, str, dict[str, Any]]] = field(
        default_factory=collections.deque
    )
    _ready: threading.Condition = field(default_factory=threading.Condition)

    def sees(self, faction: int | None) -> bool:
        return self.faction is None or (faction is not None and faction == self.faction)

    def offer(self, item: tuple[int, str, dict[str, Any]]) -> None:
        """Enqueue without waiting on the reader; drop the oldest when full."""
        with self._ready:
            if len(self._events) >= self.maxlen:
                self._events.popleft()
                self.dropped += 1
            self._events.append(item)
            self._ready.notify()

    def get(self, timeout: float) -> tuple[int, str, dict[str, Any]] | None:
        """The next event, or None after ``timeout`` (the caller sends a heartbeat)."""
        with self._ready:
            if not self._events and not self.closed:
                self._ready.wait(timeout)
            return self._events.popleft() if self._events else None


@dataclass
class _Open:
    key: str
    faction: int | None
    surface_id: str | None
    turn: int | None
    raised: float
    raised_at: str
    claimed_by: str | None = None
    claimed: float | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _cost(record: DecisionRecord) -> float | None:
    extra = record.model_extra or {}
    raw = extra.get("cost_usd", extra.get("usd"))
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


class LiveStream:
    """Bounded in-memory fan-out of the decision lifecycle, filtered per viewer."""

    def __init__(
        self,
        budgets: Mapping[int, float | None] | None = None,
        queue_size: int = DEFAULT_QUEUE,
    ) -> None:
        self._lock = threading.Lock()
        self._subs: list[Subscription] = []
        self._seq = itertools.count(1)
        self._keys = itertools.count(1)
        self._queue_size = queue_size
        self._open: dict[str, _Open] = {}
        self._local = threading.local()
        #: traceparent -> faction, so an engine outcome (which names no faction) is routed.
        self._trace_faction: collections.OrderedDict[str, int | None] = collections.OrderedDict()
        #: Per-seat spend, from the cost the producer recorded. Only seats with a recorded cost
        #: or a configured budget appear: an absent meter is "no data", never "$0".
        self._budgets: dict[int, float | None] = dict(budgets or {})
        self._spend: dict[int, float] = {}
        self.published = 0

    # ------------------------------------------------------------------ subscribers

    def subscribe(self, faction: int | None) -> Subscription:
        sub = Subscription(faction=faction, maxlen=self._queue_size)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
        with sub._ready:
            sub.closed = True
            sub._ready.notify_all()

    @property
    def dropped(self) -> int:
        with self._lock:
            return sum(s.dropped for s in self._subs)

    @property
    def viewers(self) -> int:
        with self._lock:
            return len(self._subs)

    def _publish(self, kind: str, faction: int | None, data: dict[str, Any]) -> None:
        payload = {"kind": kind, "faction_id": faction, "at": _now_iso(), **data}
        with self._lock:
            seq = next(self._seq)
            self.published += 1
            targets = [s for s in self._subs if s.sees(faction)]
        for sub in targets:
            sub.offer((seq, kind, payload))

    # ------------------------------------------------------------------ lifecycle

    def raised(self, world_view: WorldView) -> str:
        """A decision has arrived. Remembered on this thread so its record closes it."""
        key = world_view.traceparent() or f"live-{next(self._keys)}"
        entry = _Open(
            key=key,
            faction=world_view.faction_id,
            surface_id=world_view.surface_id,
            turn=world_view.turn,
            raised=time.monotonic(),
            raised_at=_now_iso(),
        )
        with self._lock:
            self._open[key] = entry
        self._local.key = key
        self._publish(
            "raised",
            entry.faction,
            {"decision": key, "surface_id": entry.surface_id, "turn": entry.turn},
        )
        return key

    def claimed(self, faction: int | None, surface_id: str | None, by: str) -> None:
        """An agent took a decision. Matched to the oldest unclaimed open one for that faction."""
        with self._lock:
            entry = next(
                (
                    o
                    for o in self._open.values()
                    if o.claimed is None and o.faction == faction and o.surface_id == surface_id
                ),
                None,
            )
            if entry is not None:
                entry.claimed, entry.claimed_by = time.monotonic(), by
        self._publish(
            "claimed",
            faction,
            {"decision": entry.key if entry else None, "surface_id": surface_id, "seat": by},
        )

    def settled(self) -> None:
        """The /decide call returned; drop anything its record did not close (a crash path)."""
        key = getattr(self._local, "key", None)
        self._local.key = None
        if key is None:
            return
        with self._lock:
            entry = self._open.pop(key, None)
        if entry is not None:
            self._publish("abandoned", entry.faction, {"decision": key})

    def write(self, record: DecisionRecord) -> None:
        """The Sink half: answered, then the guard's verdict when one ran."""
        key = getattr(self._local, "key", None) or record.trace_id
        with self._lock:
            entry = self._open.pop(key, None) if key else None
            self._local.key = None
            faction = record.seat if record.seat is not None else (entry.faction if entry else None)
            if record.trace_id:
                self._trace_faction[record.trace_id] = faction
                while len(self._trace_faction) > 1024:
                    self._trace_faction.popitem(last=False)
            cost = _cost(record)
            if cost is not None and faction is not None:
                self._spend[faction] = self._spend.get(faction, 0.0) + cost
        self._publish(
            "answered",
            faction,
            {
                "decision": key,
                "surface_id": record.surface_id,
                "turn": record.turn,
                "tier": record.tier,
                "degraded": record.degraded,
                "chosen": [c.get("action_id", c.get("id")) for c in record.chosen],
                "latency_ms": record.latency_ms,
                "cost_usd": cost,
            },
        )
        block = record.knowledge
        if block.hank_verdict is not None or block.hank_degraded:
            self._publish(
                "guard",
                faction,
                {
                    "decision": key,
                    "verdict": block.hank_verdict,
                    "degraded": block.hank_degraded,
                    "stripped": list(block.stripped),
                },
            )

    def write_policy(self, event: dict[str, Any]) -> None:
        """A ``policy_change`` record, after the JSONL has it (``Emitter.emit_policy``)."""
        faction = event.get("faction_id")
        data = {k: v for k, v in event.items() if k not in ("kind", "faction_id")}
        self._publish("policy", faction if isinstance(faction, int) else None, data)

    def outcome(self, traceparent: str, event: str, surface_id: str | None = None) -> None:
        """The engine's report: ``applied`` or ``diverged``."""
        with self._lock:
            faction = self._trace_faction.get(traceparent)
        kind = "diverged" if event == "divergence" else "applied"
        self._publish(kind, faction, {"decision": traceparent, "surface_id": surface_id})

    # ------------------------------------------------------------------ Now

    def now(self, faction: int | None) -> dict[str, Any]:
        """What is open this second, for one viewer. Ages are seconds at the time of reading."""
        clock = time.monotonic()
        with self._lock:
            rows = [
                {
                    "decision": o.key,
                    "faction_id": o.faction,
                    "surface_id": o.surface_id,
                    "turn": o.turn,
                    "raised_at": o.raised_at,
                    "age_s": round(clock - o.raised, 1),
                    "state": "claimed" if o.claimed is not None else "raised",
                    "thinking": o.claimed_by,
                    "thinking_s": round(clock - o.claimed, 1) if o.claimed is not None else None,
                }
                for o in self._open.values()
                if faction is None or o.faction == faction
            ]
            seats = sorted(set(self._budgets) | set(self._spend))
            spend = [
                {
                    "faction_id": fid,
                    "spent_usd": round(self._spend[fid], 4) if fid in self._spend else None,
                    "budget_usd": self._budgets.get(fid),
                }
                for fid in seats
                if (faction is None or fid == faction)
                and (fid in self._spend or self._budgets.get(fid) is not None)
            ]
        return {"open": rows, "spend": spend}


class ViewerTokens:
    """Who may open the stream: a seat token (player view) or the spectator token."""

    def __init__(self, seat_tokens: Any | None = None, spectator: str | None = None) -> None:
        self.seat_tokens = seat_tokens
        self.spectator = spectator or secrets.token_urlsafe(32)

    def view_for(self, token: str | None) -> tuple[str, int | None] | None:
        """``("spectator", None)``, ``("player", faction)``, or None for no/unknown token."""
        if not token:
            return None
        if secrets.compare_digest(token, self.spectator):
            return ("spectator", None)
        fid = self.seat_tokens.faction_for(token) if self.seat_tokens is not None else None
        return ("player", fid) if fid is not None else None

    def write(self, run_dir: Path) -> Path:
        """``<run>/spectator/token``, 0600, like each seat's."""
        path = run_dir / "spectator" / "token"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, self.spectator.encode())
        finally:
            os.close(fd)
        os.chmod(path, 0o600)
        return path


class RunRecord:
    """The run's own record, ``<run>/run.json``; only ``observer_spoiled`` is written here.

    Opening the spectator view during a run is a fact about the result (§3.3): a player who has
    seen the other seats' reasoning is no longer playing the same game. Written once, the first
    time, with the time; later openings change nothing.
    """

    def __init__(self, run_dir: Path | None) -> None:
        self.path = run_dir / RUN_RECORD if run_dir is not None else None
        self._lock = threading.Lock()
        self.observer_spoiled_at: str | None = None

    def spoil(self) -> bool:
        """Mark the run spoiled. True only on the call that actually wrote it."""
        with self._lock:
            if self.observer_spoiled_at is not None:
                return False
            self.observer_spoiled_at = _now_iso()
            if self.path is not None:
                current: dict[str, Any] = {}
                if self.path.exists():
                    try:
                        loaded = json.loads(self.path.read_text(encoding="utf-8"))
                        current = loaded if isinstance(loaded, dict) else {}
                    except (OSError, json.JSONDecodeError):
                        current = {}
                current.update(
                    {"observer_spoiled": True, "observer_spoiled_at": self.observer_spoiled_at}
                )
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
                tmp.replace(self.path)
            return True
