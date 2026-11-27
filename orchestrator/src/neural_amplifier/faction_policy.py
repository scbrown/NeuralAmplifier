"""Per-faction policy overlay: the frozen knob registry and the store behind it (aegis-8fye.6).

Door 3 of ``docs/seats-and-policy.md`` §4: a seat sets a standing *posture* and Thinker's own AI
executes under it. This module is the orchestrator half of the first cut, which steers only the
per-faction fields the engine already has on an AI slot — they are set once at spawn, never
recomputed, and saved with the game, so overriding them needs no new seam in the fork.

**The registry is frozen, like** :mod:`.surfaces`. Every knob has a name, a type, bounds and the
engine field it maps to. The fork mirrors this table; a knob that is not here does not exist,
and a value outside its bounds is refused at *set* time — while the seat still holds it and can
correct it — never discovered later as a posture that silently did nothing.

**Exclusivity is structural.** :meth:`PolicyStore.set_policy` takes the seat's faction as the
key, and the service derives that faction from the seat token. Nothing accepts a policy and a
faction as two independent, caller-supplied arguments.

**The overlay is write-only.** ``AIPlans`` is omniscient (``plans_upkeep`` sums every faction's
units), so a seat reads back what it SET and never what Thinker derived. Nothing in this module
holds, accepts or returns plan state; :data:`PLAN_STATE_FIELDS` names it so a test can prove that
no seat-facing payload ever carries it.

**Wire shape** (the ``POST /turn`` reply, consumed by the fork in slice B)::

    {"turn": 12, "expected": 3,
     "na_policy": [{"faction_id": 3, "knobs": {"focus.fight": 1, "target.base": 41}}],
     "policy_hash": "<sha256 hex of canonical JSON of the na_policy list>"}

``na_policy`` is the COMPLETE overlay, sorted by faction id, holding only factions with an LLM
seat and at least one knob set. A faction absent from it has no overlay: the fork restores its
own value. When no faction has a knob set, both keys are omitted, and the reply is byte-for-byte
the reply a fork that predates this module already ignores.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

KnobType = Literal["int", "flag", "goals"]
#: Who set a knob, as the change log names it: a skill's ``on_turn``, a seat's agent acting on
#: its standing directives through ``/agent/policy``, or the operator.
SetBy = Literal["skill", "directive", "operator"]
SET_BY: tuple[str, ...] = ("skill", "directive", "operator")
#: The ``kind`` of a change-log record.
POLICY_CHANGE = "policy_change"

#: Faction ids an overlay can name. 0 is native life and never a player; 8 is MaxPlayerNum.
FACTION_IDS = range(1, 8)
#: ``MaxBaseNum`` in the fork's ``engine_enums.h``.
MAX_BASE_ID = 511
#: Goal coordinates are map tiles. The largest SMAC map fits inside this, and the fork bounds
#: the real map again when it applies a goal.
MAX_COORD = 511
#: Goal entries one policy may request. ``Faction.goals`` has 75 slots shared with Thinker's own.
MAX_GOALS = 8
#: ``AIGoal`` values a policy may request. Thinker-internal goals (200+) and the engine's
#: unnamed ones are not a seat's to set.
GOAL_TYPES: Mapping[str, int] = MappingProxyType(
    {"attack": 0, "defend": 2, "scout": 3, "colonize": 8}
)
GOAL_PRIORITY = (1, 20)


class KnobError(ValueError):
    """A knob that is not in the registry, or a value outside its bounds. Raised at set time."""


@dataclass(frozen=True)
class Knob:
    """One steerable per-faction field."""

    name: str
    type: KnobType
    #: The engine field this knob writes, as the fork names it.
    field: str
    lo: int = 0
    hi: int = 0
    #: For ``flag`` knobs: the bit in ``field`` this knob sets or clears.
    mask: int = 0
    doc: str = ""


def _focus(name: str, field: str, lo: int, doc: str) -> Knob:
    return Knob(f"focus.{name}", "int", f"Faction.{field}", lo, 1, doc=doc)


def _flag(name: str, bit: str, mask: int, doc: str) -> Knob:
    return Knob(f"strategy.{name}", "flag", "Faction.player_flags", mask=mask, doc=f"{bit}: {doc}")


#: The frozen registry. Adding a knob is a reviewed change here AND in the fork's mirror.
#:
#: The ``focus.*`` bounds are the range faction files use (``AI_fight`` -1..1, the rest 0..1);
#: Thinker reads them as weights in about 60 places, so a value outside that range is a posture
#: no stock faction has ever been tested under.
#:
#: The ``strategy.*`` subset is the strategy bits only. The diplomatic and atrocity bits
#: (``PFLAG_COOP_WITH_HUMAN``, ``PFLAG_TEAM_UP_VS_HUMAN``, ``PFLAG_COMMIT_ATROCITIES_WANTONLY``,
#: ``PFLAG_OBLITERATE_CAPTURED_BASES``) and the engine's bookkeeping bits are not steerable.
KNOBS: Mapping[str, Knob] = MappingProxyType(
    {
        k.name: k
        for k in (
            _focus("fight", "AI_fight", -1, "aggression weight: combat builds, social, nukes"),
            _focus("growth", "AI_growth", 0, "growth weight: expansion, population"),
            _focus("tech", "AI_tech", 0, "tech weight: research, labs"),
            _focus("wealth", "AI_wealth", 0, "wealth weight: energy, economy"),
            _focus("power", "AI_power", 0, "power weight: military techs and builds"),
            _flag("attack_enemy_hq", "PFLAG_STRAT_ATK_ENEMY_HQ", 0x200000, "go for the capital"),
            _flag("land_power", "PFLAG_EMPHASIZE_LAND_POWER", 0x4000000, "favour land units"),
            _flag("sea_power", "PFLAG_EMPHASIZE_SEA_POWER", 0x8000000, "favour naval units"),
            _flag("air_power", "PFLAG_EMPHASIZE_AIR_POWER", 0x10000000, "favour air units"),
            _flag("search_objectives", "PFLAG_STRAT_SEARCH_OBJECTIVES", 0x20000000, "explore"),
            _flag("defend_objectives", "PFLAG_STRAT_DEF_OBJECTIVES", 0x40000000, "hold"),
            _flag("attack_objectives", "PFLAG_STRAT_ATK_OBJECTIVES", 0x80000000, "take"),
            Knob(
                "target.base",
                "int",
                "Faction.base_id_attack_target",
                -1,
                MAX_BASE_ID,
                doc="base id to attack; -1 is no target",
            ),
            Knob(
                "target.goals",
                "goals",
                "Faction.goals",
                0,
                MAX_GOALS,
                doc="up to 8 {type, x, y, priority} goals; type is attack/defend/scout/colonize",
            ),
        )
    }
)

#: ``AIPlans`` field names in the fork (``main.h``). Computed every turn from every faction's
#: units — omniscient — so none of these may appear in anything a seat reads.
PLAN_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "main_region", "main_region_x", "main_region_y", "main_sea_region",
        "target_land_region", "prioritize_naval", "naval_scout_x", "naval_scout_y",
        "naval_airbase_x", "naval_airbase_y", "naval_start_x", "naval_start_y",
        "naval_end_x", "naval_end_y", "naval_beach_x", "naval_beach_y",
        "land_combat_units", "sea_combat_units", "air_combat_units", "probe_units",
        "missile_units", "transport_units", "unknown_factions", "contacted_factions",
        "enemy_factions", "build_tubes", "project_limit", "median_limit", "energy_limit",
        "psi_score", "keep_fungus", "plant_fungus", "satellite_goal", "enemy_odp", "enemy_sat",
        "mil_strength", "defense_modifier", "max_offense_value", "max_defense_value",
        "enemy_base_range", "enemy_mil_factor", "enemy_bases", "captured_bases", "plans",
    }
)  # fmt: skip


def _int(knob: Knob, value: object, lo: int, hi: int, what: str = "") -> int:
    label = f"{knob.name}{what}"
    if isinstance(value, bool) or not isinstance(value, int):
        raise KnobError(f"{label}: expected an integer, got {value!r}")
    if not lo <= value <= hi:
        raise KnobError(f"{label}: {value} is outside {lo}..{hi}")
    return value


def _goals(knob: Knob, value: object) -> list[dict[str, int | str]]:
    if not isinstance(value, list):
        raise KnobError(f"{knob.name}: expected a list of goals, got {value!r}")
    if len(value) > MAX_GOALS:
        raise KnobError(f"{knob.name}: {len(value)} goals; at most {MAX_GOALS}")
    out: list[dict[str, int | str]] = []
    for i, goal in enumerate(value):
        at = f"[{i}]"
        if not isinstance(goal, dict):
            raise KnobError(f"{knob.name}{at}: expected an object, got {goal!r}")
        extra = set(goal) - {"type", "x", "y", "priority"}
        if extra:
            raise KnobError(f"{knob.name}{at}: unknown fields {sorted(extra)}")
        kind = goal.get("type")
        if kind not in GOAL_TYPES:
            raise KnobError(f"{knob.name}{at}.type: {kind!r} is not one of {sorted(GOAL_TYPES)}")
        out.append(
            {
                "type": str(kind),
                "x": _int(knob, goal.get("x"), 0, MAX_COORD, f"{at}.x"),
                "y": _int(knob, goal.get("y"), 0, MAX_COORD, f"{at}.y"),
                "priority": _int(knob, goal.get("priority"), *GOAL_PRIORITY, f"{at}.priority"),
            }
        )
    return out


def validate(name: str, value: object) -> Any:
    """The value a knob will carry on the wire, or :class:`KnobError`. Unknown is refused."""
    knob = KNOBS.get(name)
    if knob is None:
        raise KnobError(f"unknown knob {name!r}; the registry has {sorted(KNOBS)}")
    if knob.type == "flag":
        if not isinstance(value, bool):
            raise KnobError(f"{name}: expected true or false, got {value!r}")
        return value
    if knob.type == "goals":
        return _goals(knob, value)
    return _int(knob, value, knob.lo, knob.hi)


def canonical(obj: object) -> bytes:
    """The one serialisation a hash is taken over: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def policy_hash(obj: object) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


#: The hash a decision record carries when its faction has no overlay: Thinker's own defaults.
DEFAULT_POLICY_HASH = policy_hash({})


def registry() -> list[dict[str, object]]:
    """The registry as a seat (or the fork's mirror test) reads it."""
    rows: list[dict[str, object]] = []
    for knob in KNOBS.values():
        row: dict[str, object] = {"name": knob.name, "type": knob.type, "field": knob.field}
        if knob.type == "int":
            row.update(lo=knob.lo, hi=knob.hi)
        elif knob.type == "flag":
            row["mask"] = knob.mask
        else:
            row.update(max=knob.hi, goal_types=sorted(GOAL_TYPES), priority=list(GOAL_PRIORITY))
        row["doc"] = knob.doc
        rows.append(row)
    return rows


def _json_safe(value: object) -> object:
    """A refused value is recorded as given, but only as JSON can carry it."""
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return repr(value)


def change_record(
    faction_id: int,
    knob: str,
    old: object,
    new: object,
    reason: object,
    set_by: str,
    turn: int | None,
    refused: str | None = None,
) -> dict[str, Any]:
    """One ``policy_change`` line: time, turn, knob, old -> new, reason, who, and the outcome.

    A refused change is recorded too, with ``new`` as it was asked for and the refusal text, so
    a seat that keeps asking for a posture it cannot have is visible rather than silent.
    """
    return {
        "kind": POLICY_CHANGE,
        "schema_version": "0.1",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "turn": turn,
        "faction_id": faction_id,
        "knob": knob,
        "old": _json_safe(old),
        "new": _json_safe(new),
        "reason": reason if isinstance(reason, str) else _json_safe(reason),
        "set_by": set_by,
        "outcome": "refused" if refused is not None else "set",
        "refusal": refused,
    }


class PolicyStore:
    """Each faction's knobs as set, and the overlay last delivered to the engine.

    A set takes effect at the next ``/turn`` reply, which is when the engine receives it and
    before that turn's decisions are made. Decision records therefore carry the hash of what was
    last DELIVERED for their faction, not of what has been set since: a record states the posture
    the engine was actually under.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._knobs: dict[int, dict[str, Any]] = {}
        self._reasons: dict[int, dict[str, str]] = {}
        self._delivered: dict[int, str] = {}
        #: Receives each ``policy_change`` record: the service points it at
        #: ``Emitter.emit_policy``, so the change log is JSONL first, then the live stream.
        self.observer: Callable[[dict[str, Any]], None] | None = None
        self.observer_failures = 0

    def set_policy(
        self,
        faction_id: int,
        knob: str,
        value: object,
        reason: str,
        *,
        set_by: SetBy = "operator",
        turn: int | None = None,
    ) -> dict[str, object]:
        """Set (or, with ``value=None``, clear) one knob for a seat's own faction.

        Refuses an unknown knob, an out-of-bounds value, or a change with no reason — a posture
        nobody can explain later reads as a plan somebody is watching.

        Every call, accepted or refused, is handed to :attr:`observer` as one ``policy_change``
        record (the console's change log, ``seats-and-policy.md`` §7). The history is that
        record and nothing else: it is never rebuilt from the knobs currently in force.
        """
        try:
            if faction_id not in FACTION_IDS:
                raise KnobError(f"faction {faction_id} cannot carry a policy (1..7)")
            if not isinstance(reason, str) or not reason.strip():
                raise KnobError("a policy change needs a reason")
            if knob not in KNOBS:
                raise KnobError(f"unknown knob {knob!r}; the registry has {sorted(KNOBS)}")
            checked = None if value is None else validate(knob, value)
        except KnobError as exc:
            with self._lock:
                previous = self._knobs.get(faction_id, {}).get(knob)
            self._observe(
                change_record(
                    faction_id, knob, previous, value, reason, set_by, turn, refused=str(exc)
                )
            )
            raise
        with self._lock:
            knobs = self._knobs.setdefault(faction_id, {})
            reasons = self._reasons.setdefault(faction_id, {})
            previous = knobs.get(knob)
            if checked is None:
                knobs.pop(knob, None)
                reasons.pop(knob, None)
            else:
                knobs[knob] = checked
                reasons[knob] = reason.strip()
        self._observe(change_record(faction_id, knob, previous, checked, reason, set_by, turn))
        return {
            "faction_id": faction_id,
            "knob": knob,
            "value": checked,
            "superseded": previous,
            "status": "set; it reaches the engine with the next turn announcement",
        }

    def _observe(self, event: dict[str, Any]) -> None:
        """Hand one change record to the observer; a broken observer never refuses a set."""
        if self.observer is None:
            return
        try:
            self.observer(event)
        except Exception:  # noqa: BLE001 — the change log must never stall a seat
            self.observer_failures += 1

    def factions(self) -> list[int]:
        """Factions with at least one knob set."""
        with self._lock:
            return sorted(fid for fid, knobs in self._knobs.items() if knobs)

    def knobs(self, faction_id: int) -> dict[str, Any]:
        """What this faction's seat has SET. Never anything Thinker computed."""
        with self._lock:
            return json.loads(canonical(self._knobs.get(faction_id, {})))  # type: ignore[no-any-return]

    def reply(self, llm_factions: Iterable[int]) -> dict[str, object]:
        """The ``/turn`` reply fields, and the delivery they record. Empty when nothing is set."""
        allowed = set(llm_factions)
        with self._lock:
            rows = [
                {"faction_id": fid, "knobs": json.loads(canonical(knobs))}
                for fid, knobs in sorted(self._knobs.items())
                if knobs and fid in allowed
            ]
            self._delivered = {int(row["faction_id"]): policy_hash(row["knobs"]) for row in rows}
        if not rows:
            return {}
        return {"na_policy": rows, "policy_hash": policy_hash(rows)}

    def hash_for(self, faction_id: int | None) -> str:
        """The hash of the overlay the engine is under for this faction; the default if none."""
        if faction_id is None:
            return DEFAULT_POLICY_HASH
        with self._lock:
            return self._delivered.get(faction_id, DEFAULT_POLICY_HASH)
