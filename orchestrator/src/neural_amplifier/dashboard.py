"""Read-only live-game dashboard projections.

The dashboard deliberately reads the artifacts the run already writes.  It never calls
``/decide`` or an agent endpoint, and it never invents a second metric calculation: live facts
come from stored world views, decision facts from ``DecisionRecord``, and eval tables from the
published scorers.
"""

# The embedded HTML/CSS/JavaScript is deliberately dependency-free and has naturally long lines.
# ruff: noqa: E501

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .decisions import DecisionLog, DecisionRecord
from .replay import WorldViewStore

REPO = Path(__file__).resolve().parents[3]
FACTION_COLOURS = ("#62d6ff", "#ff4545", "#f4e04d", "#55e06f", "#c880ff", "#ff9f43", "#eeeeee")
SMAC_FACTIONS = {
    1: ("Gaians", "#55e06f"),
    2: ("Hive", "#f4e04d"),
    3: ("University", "#eeeeee"),
    4: ("Morganites", "#ff9f43"),
    5: ("Spartans", "#222222"),
    6: ("Believers", "#ff4545"),
    7: ("Peacekeepers", "#62d6ff"),
}


def _choice_id(choice: dict[str, Any]) -> str:
    return str(choice.get("action_id", choice.get("id", "—")))


def _record_cost(record: DecisionRecord) -> float | None:
    """Model spend when a producer recorded it; never confuse it with game energy credits."""
    extra = record.model_extra or {}
    raw = extra.get("cost_usd", extra.get("usd"))
    return float(raw) if isinstance(raw, int | float) else None


#: Directive dispositions, most-decision-relevant first. ``in_force`` alone is not a
#: disposition — it is the population every other list partitions.
_DISPOSITIONS = ("followed", "overrode", "unsatisfied", "unmeasurable", "rejected", "conflicts")


def directive_dispositions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every in-force directive with what actually became of it.

    The panel used to render ``in_force``/``followed``/``overrode`` as three flat lists, which
    cannot answer the question the reader is actually asking — *which directive bound this
    choice, and what happened to the rest?*  A directive in force and absent from ``followed``
    reads as "the model ignored it", when on the measured data it is almost always
    ``unsatisfied``: MEASURED across 610 ladder decisions, ``unsatisfied`` is populated on 609
    and was rendered on none of them, while ``followed`` covers 71%.  Dropping the field that
    explains the gap is what made an empty ``FOLLOWED`` ambiguous.
    """
    in_force = [str(d) for d in plan.get("in_force") or []]
    seen: dict[str, list[str]] = {d: [] for d in in_force}
    for key in _DISPOSITIONS:
        for raw in plan.get(key) or []:
            seen.setdefault(str(raw), []).append(key)
    return [
        {"id": d, "dispositions": seen.get(d) or [], "in_force": d in seen and d in in_force}
        for d in sorted(seen, key=lambda x: (x not in in_force, x))
    ]


def grounding_state(knowledge: dict[str, Any], view: dict[str, Any] | None) -> dict[str, Any]:
    """Grounding facts, and WHICH KIND of nothing when there are none.

    Three different conditions all render as an empty fact list, and the orchestrator already
    tells them apart — ``quipu_absent`` (no retriever was configured), ``quipu_degraded``
    (configured and it failed), and a real query that matched nothing.  Flattening them into
    "no facts" would destroy a distinction the data makes: MEASURED, ladder-attempt4 is
    ``absent`` on 610/610 decisions and arm A is ``degraded`` on 193/193, and a panel that
    printed "—" for both would report the second as though retrieval had simply found nothing.
    """
    facts = [str(f) for f in (knowledge.get("quipu_facts") or [])]
    if not facts and view:
        facts = [str(f) for f in (view.get("grounding") or [])]
    cited = {str(c) for c in (knowledge.get("quipu_cited") or [])}
    hits = int(knowledge.get("quipu_hits") or 0)
    if knowledge.get("quipu_absent"):
        state, label = "absent", "NO RETRIEVER CONFIGURED"
    elif knowledge.get("quipu_degraded"):
        state, label = "degraded", "RETRIEVAL FAILED"
    elif facts or hits:
        state, label = "present", f"{hits or len(facts)} FACTS RETRIEVED"
    else:
        state, label = "empty", "QUERIED // 0 FACTS MATCHED"
    return {
        "state": state,
        "label": label,
        "hits": hits,
        "latency_ms": knowledge.get("quipu_latency_ms"),
        # A fact counts as cited when its id prefix appears in ``quipu_cited``; the contract
        # says each grounding entry starts with its id (contract.py: "e.g. `unit:colony-pod`").
        "facts": [{"text": f, "cited": any(f.startswith(c) for c in cited)} for f in facts],
    }


#: Read-only graph browsing. The dashboard NEVER writes to the graph and never calls the brain;
#: it forwards two shapes of read to whatever Quipu the run was pointed at.
#: The page is read by someone who manages a GAME, not by someone who maintains this service
#: (Stiwi, 2026-08-24: "degraded" meant nothing to him). Every internal term the page shows gets
#: a plain-language name and a one-line explanation, defined ONCE here in Python so the glossary
#: is unit-testable and the HTML cannot drift from it.
PLAIN_TIER = {
    "llm": ("LLM decided", "The language model chose this action."),
    "deterministic": (
        "Engine decided",
        "The game engine's own logic chose this, with no model involved.",
    ),
    "deferred": ("Put off", "The decision was parked to be answered later."),
    "queued": ("Waiting", "The decision is queued and has not been answered yet."),
    "plan": ("From the plan", "A pre-set plan entry supplied this action."),
}

PLAIN_DISPOSITION = {
    "followed": ("Followed", "This turn's choice obeyed this directive."),
    "overrode": ("Overrode", "The model deliberately went against this directive."),
    "unsatisfied": (
        "Goal not met yet",
        "About the GOAL, not this choice: the directive's target has not been reached. A"
        " directive can be followed this turn and still have its goal unmet.",
    ),
    "unmeasurable": (
        "Could not check",
        "The number this directive tracks was not in view, so it could not be checked here.",
    ),
    "rejected": ("Rejected", "The directive was refused when it was issued."),
    "conflicts": ("Conflicts", "This directive pulls against another one in force."),
}

PLAIN_GROUNDING = {
    "absent": (
        "No knowledge graph connected",
        "This run was not given a graph, so nothing was ever looked up.",
    ),
    "degraded": (
        "Knowledge graph FAILED",
        "A graph was connected and it did not answer. The model decided without facts it should have had.",
    ),
    "empty": ("Nothing relevant found", "The graph answered and had no fact for this situation."),
    "present": ("Facts retrieved", "These facts were looked up and shown to the model."),
}

PLAIN_FALLBACK = (
    "Fallback — engine default used",
    "The language model could not be reached, so the engine's safe default action was applied"
    " instead. The reasoning below is not the model's.",
)


def glossary() -> dict[str, Any]:
    """Every internal term the page can show, with its plain name and explanation."""
    return {
        "tier": {k: {"name": v[0], "help": v[1]} for k, v in PLAIN_TIER.items()},
        "disposition": {k: {"name": v[0], "help": v[1]} for k, v in PLAIN_DISPOSITION.items()},
        "grounding": {k: {"name": v[0], "help": v[1]} for k, v in PLAIN_GROUNDING.items()},
        "fallback": {"name": PLAIN_FALLBACK[0], "help": PLAIN_FALLBACK[1]},
    }


def run_state(live: dict[str, Any], paused_after: float = 120.0) -> dict[str, Any]:
    """LIVE / PAUSED / NO RUN, in words, with how long it has been that way.

    A page that has simply gone quiet is indistinguishable from a broken one, and the old
    banner said only "IDLE SINCE <timestamp>" — which on a row stopped 79 minutes ago reads as
    a page that failed to load rather than a game that is not running. Idle is a fact about the
    ARTIFACTS; the reader wants a fact about the GAME.
    """
    if not live.get("configured"):
        return {
            "state": "no-run",
            "headline": "NO RUN CONNECTED",
            "detail": "This dashboard has not been pointed at a game's decision log.",
        }
    idle = live.get("idle_seconds")
    if live.get("active"):
        return {
            "state": "live",
            "headline": f"LIVE // TURN {live.get('turn') or '—'}",
            "detail": f"{live.get('decisions') or 0} decisions so far. The game is advancing.",
        }
    if idle is None:
        return {
            "state": "paused",
            "headline": "PAUSED",
            "detail": "A run is connected but has never written a decision.",
        }
    minutes = idle / 60.0
    span = (
        f"{idle:.0f} seconds"
        if idle < 120
        else (f"{minutes:.0f} minutes" if minutes < 120 else f"{minutes / 60:.1f} hours")
    )
    return {
        "state": "paused",
        "headline": f"PAUSED // NO NEW DECISION FOR {span.upper()}",
        "detail": (
            f"The game reached turn {live.get('turn') or '—'} and stopped there."
            " The page is up to date — this is the game not running, not a broken page."
        ),
        "idle_seconds": idle,
    }


def _graph_post(base: str, path: str, payload: dict[str, Any], timeout: float = 8.0) -> Any:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())


def graph_view(
    base: str | None,
    query: str | None = None,
    entity: str | None = None,
    post: Any = _graph_post,
) -> dict[str, Any]:
    """The NA knowledge graph, browsable, with the reason for an empty result named.

    Three different conditions yield no entities and they are not the same fact: no graph was
    configured for this run, one was configured and could not be reached, and a real query that
    matched nothing. The first two are the operator's problem and the third is the graph's, so
    collapsing them into an empty list would hide a broken link behind "no results" — the same
    rule the grounding badges follow.
    """
    if not base:
        return {
            "state": "unconfigured",
            "detail": "No graph was configured for this run.",
            "rows": [],
        }
    try:
        if entity:
            payload = {
                "query": (
                    "SELECT ?p ?o WHERE { <" + entity.replace(">", "") + "> ?p ?o } LIMIT 200"
                )
            }
            body = post(base, "/query", payload)
            rows = [{"predicate": r.get("p"), "object": r.get("o")} for r in body.get("rows") or []]
            mode = "entity"
        elif query:
            body = post(base, "/search", {"query": query})
            rows = [
                {
                    "entity": r.get("entity"),
                    "score": r.get("score"),
                    "text": r.get("text"),
                }
                for r in body.get("results") or []
            ]
            mode = "search"
        else:
            body = post(
                base,
                "/query",
                {
                    "query": (
                        "SELECT ?t (COUNT(?s) AS ?n) WHERE { ?s a ?t } "
                        "GROUP BY ?t ORDER BY DESC(?n)"
                    )
                },
            )
            rows = [
                {"type": str(r.get("t")).rsplit("/", 1)[-1], "iri": r.get("t"), "count": r.get("n")}
                for r in body.get("rows") or []
            ]
            mode = "census"
    except Exception as exc:  # noqa: BLE001 - the reason is the payload
        return {
            "state": "unreachable",
            "detail": f"{type(exc).__name__}: {exc}",
            "base": base,
            "rows": [],
        }
    return {
        "state": "ok" if rows else "empty",
        "mode": mode,
        "base": base,
        "rows": rows,
        "detail": "" if rows else "The graph answered, and matched nothing.",
    }


class DashboardReader:
    """Small, bounded projections over an append-only run."""

    def __init__(
        self,
        log: DecisionLog | None,
        store: WorldViewStore | None,
        game_state: Path | None = None,
        query_game_state: bool = False,
    ) -> None:
        self.log = log
        self.store = store
        self.game_state = game_state
        self.query_game_state = query_game_state
        self._game_state_lock = threading.Lock()
        self._eval_cache: tuple[int, dict[str, Any]] | None = None

    def faction_census(self) -> dict[int, int]:
        """All-player base counts from Thinker's observer result, if it has answered."""
        if self.game_state is None:
            return {}
        command = self.game_state.with_name("na-command")
        if self.query_game_state:
            with self._game_state_lock:
                self.game_state.unlink(missing_ok=True)
                command.write_text("game-state", encoding="utf-8")
                deadline = time.monotonic() + 2.0
                while not self.game_state.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
        if not self.game_state.is_file():
            return {}
        try:
            payload = json.loads(self.game_state.read_text(encoding="utf-8"))
            token = next(
                part for part in str(payload.get("detail", "")).split() if part.startswith("bases=")
            )
            return {
                int(faction): int(count)
                for item in token.removeprefix("bases=").split(",")
                for faction, count in [item.split(":", 1)]
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError, StopIteration):
            return {}

    def records(self) -> list[DecisionRecord]:
        return list(self.log.read()) if self.log is not None else []

    def world_view(self, record: DecisionRecord) -> dict[str, Any] | None:
        view = self.store.get(record.world_view_hash) if self.store is not None else None
        return view.model_dump(mode="json") if view is not None else None

    def live(self) -> dict[str, Any]:
        records = self.records()
        updated_at = (
            datetime.fromtimestamp(self.log.path.stat().st_mtime, UTC)
            if self.log is not None and self.log.path.is_file()
            else None
        )
        idle_seconds = (
            max(0.0, (datetime.now(UTC) - updated_at).total_seconds()) if updated_at else None
        )
        latest: dict[str, tuple[DecisionRecord, dict[str, Any]]] = {}
        total_cost = 0.0
        for record in records:
            view = self.world_view(record)
            # A log without its content-addressed store is incomplete evidence, but the record
            # still knows the faction and turn. Show that row with blanks instead of making a
            # live faction disappear; never back-fill metrics from guesses.
            facts = view or {}
            faction = str(facts.get("faction") or record.faction)
            latest[faction] = (record, facts)
            total_cost += _record_cost(record) or 0

        factions_by_id: dict[int, dict[str, Any]] = {}
        for faction_id, bases in self.faction_census().items():
            name, colour = SMAC_FACTIONS.get(
                faction_id,
                (f"Faction {faction_id}", FACTION_COLOURS[(faction_id - 1) % len(FACTION_COLOURS)]),
            )
            factions_by_id[faction_id] = {
                "name": name,
                "id": faction_id,
                "colour": colour,
                "turn": None,
                "bases": bases,
                "population": None,
                "minerals": None,
                "energy": None,
                "income": None,
                "labs": None,
                "military": None,
                "techs": None,
            }
        unnumbered: list[dict[str, Any]] = []
        for index, (name, (record, view)) in enumerate(sorted(latest.items())):
            raw_metrics = view.get("metrics")
            metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
            item = {
                "name": name,
                "id": view.get("faction_id"),
                "colour": view.get("faction_colour")
                or view.get("faction_color")
                or FACTION_COLOURS[index % len(FACTION_COLOURS)],
                "turn": record.turn,
                "bases": metrics.get("base_count"),
                "population": metrics.get("pop_total"),
                "minerals": metrics.get("mineral_output", metrics.get("mineral_surplus")),
                "energy": metrics.get("energy_reserves", view.get("energy_reserves")),
                "income": metrics.get("energy_income"),
                "labs": metrics.get("labs_output"),
                "military": metrics.get("military_units"),
                "techs": metrics.get("tech_count"),
            }
            observed_id = view.get("faction_id")
            if isinstance(observed_id, int):
                census = factions_by_id.get(observed_id, {})
                merged = {**census, **item}
                if census.get("bases") is not None:
                    merged["bases"] = census["bases"]
                factions_by_id[observed_id] = merged
            else:
                unnumbered.append(item)

        factions = [factions_by_id[key] for key in sorted(factions_by_id)] + unnumbered

        newest = max(records, key=lambda r: r.turn, default=None)
        newest_view = self.world_view(newest) if newest is not None else None
        fairness = (newest_view or {}).get("fairness") or (
            {"handicaps": newest.fairness_profile} if newest is not None else {}
        )
        return {
            "configured": self.log is not None,
            "active": idle_seconds is not None and idle_seconds < 15,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "idle_seconds": round(idle_seconds, 1) if idle_seconds is not None else None,
            # computed here rather than in the page so the wording is testable
            "run_state": None,  # filled below
            "game_id": newest.game_id if newest is not None else None,
            "turn": newest.turn if newest is not None else None,
            "decisions": len(records),
            "spend": round(total_cost, 4),
            "fairness": fairness,
            "run_id": (newest_view or {}).get("run_id"),
            "seed": (newest_view or {}).get("seed", (newest_view or {}).get("map_seed")),
            "arm": (newest_view or {}).get("arm", (newest_view or {}).get("tier")),
            "victory": (newest_view or {}).get("victory")
            or (newest_view or {}).get("victory_state"),
            "factions": factions,
        }

    def live_with_state(self) -> dict[str, Any]:
        """``live()`` plus the plain-language run state the banner shows."""
        payload = self.live()
        payload["run_state"] = run_state(payload)
        return payload

    def decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        all_records = self.records()
        records = all_records[-max(1, min(limit, 500)) :]
        out = []
        start = max(0, len(all_records) - len(records))
        for position, record in enumerate(records, start=start):
            chosen = [_choice_id(c) for c in record.chosen]
            view = self.world_view(record)
            native = (view or {}).get("native_choice")
            out.append(
                {
                    "id": position,
                    "turn": record.turn,
                    "faction": record.faction,
                    "surface": record.surface_id,
                    "tier": record.tier,
                    "degraded": record.degraded,
                    "chosen": chosen,
                    "native": native,
                    "disagreed": native is not None and str(native) not in chosen,
                    "reason": record.reason,
                    "latency_ms": record.latency_ms,
                    "cost": _record_cost(record),
                }
            )
        return list(reversed(out))

    def decision(self, position: int) -> dict[str, Any] | None:
        records = self.records()
        if position < 0 or position >= len(records):
            return None
        record = records[position]
        view = self.world_view(record)
        dumped = record.model_dump(mode="json")
        plan = dumped.get("plan") or {}
        knowledge = dumped.get("knowledge") or {}
        return {
            "record": dumped,
            "world_view": view,
            "action_space": (view or {}).get("action_space", []),
            "native_choice": (view or {}).get("native_choice"),
            "native_choice_name": (view or {}).get("native_choice_name"),
            "why": {
                "directives": directive_dispositions(plan),
                "plan_absent": bool(plan.get("plan_absent")),
                "grounding": grounding_state(knowledge, view),
                "guard": {
                    "verdict": knowledge.get("hank_verdict"),
                    "absent": bool(knowledge.get("hank_absent")),
                    "degraded": bool(knowledge.get("hank_degraded")),
                    "advisories": [str(a) for a in (knowledge.get("advisories") or [])],
                    "stripped": [str(a) for a in (knowledge.get("stripped") or [])],
                },
            },
        }

    def strategy(self, plan_path: Path | None = None) -> dict[str, Any]:
        """Directives in force per turn, and what became of each one.

        Built from the decision log, which every run writes, rather than from a live
        DirectiveStore — the log is the committed artifact and it carries the disposition the
        orchestrator actually recorded at decision time. A store read would answer "what is in
        force now", which is a different question from "what was in force at turn 42".

        Directive TEXT (intent, metric, target, rationale) lives in a plan file that the
        dashboard is not necessarily given. When it is missing this reports
        ``definitions: "unavailable"`` rather than rendering bare ids as though an id were the
        whole directive — the same rule as grounding: name which kind of nothing this is.
        """
        by_turn: dict[int, dict[str, Any]] = {}
        totals: dict[str, dict[str, int]] = {}
        for record in self.records():
            plan = (record.model_dump(mode="json").get("plan")) or {}
            in_force = [str(d) for d in plan.get("in_force") or []]
            if not in_force and not plan.get("plan_absent"):
                continue
            turn = by_turn.setdefault(
                record.turn, {"turn": record.turn, "decisions": 0, "directives": {}}
            )
            turn["decisions"] += 1
            for directive in in_force:
                seat = turn["directives"].setdefault(directive, {"id": directive, "in_force": 0})
                tally = totals.setdefault(directive, {"in_force": 0})
                seat["in_force"] += 1
                tally["in_force"] += 1
                for key in _DISPOSITIONS:
                    if directive in (plan.get(key) or []):
                        seat[key] = seat.get(key, 0) + 1
                        tally[key] = tally.get(key, 0) + 1

        definitions: dict[str, Any] = {}
        source = "unavailable"
        if plan_path is not None and plan_path.exists():
            try:
                raw = json.loads(plan_path.read_text(encoding="utf-8"))
                for directive in raw.get("directives") or []:
                    definitions[str(directive.get("id"))] = directive
                source = "plan-file"
            except (OSError, ValueError):
                source = "unreadable"

        # A source of "plan-file" does NOT mean every directive has text: most are issued at
        # runtime through /agent/directive and never appear in the plan file. MEASURED on
        # ladder-attempt4: the file defines 1 directive while 8 appear in the log. Reporting only
        # the source would let seven rows render blank with nothing saying why, which is the
        # same ambiguity the grounding badges exist to remove.
        missing = sorted(d for d in totals if d not in definitions)
        return {
            "definitions": definitions,
            "definitions_source": source,
            "definitions_missing": missing,
            "definitions_covered": len(totals) - len(missing),
            "directive_count": len(totals),
            "turns": [
                {**t, "directives": sorted(t["directives"].values(), key=lambda d: d["id"])}
                for t in sorted(by_turn.values(), key=lambda t: t["turn"])
            ],
            "totals": [
                {
                    "id": directive,
                    **counts,
                    # Share of the decisions this directive was in force for that recorded it
                    # as followed. Deliberately NOT followed/(followed+unsatisfied): the two
                    # co-occur (568 times on ladder-attempt4), so that denominator would be
                    # larger than the population and the ratio would read low for no reason.
                    "followed_share": (
                        round(counts.get("followed", 0) / counts["in_force"], 3)
                        if counts.get("in_force")
                        else None
                    ),
                }
                for directive, counts in sorted(totals.items())
            ],
        }

    def evals(self) -> dict[str, Any]:
        manifest = REPO / "evals" / "published.json"
        stamp = manifest.stat().st_mtime_ns
        if self._eval_cache is not None and self._eval_cache[0] == stamp:
            return self._eval_cache[1]
        declared = json.loads(manifest.read_text(encoding="utf-8"))["runs"]
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "score_published.py"), "--show"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        result = {
            "runs": declared,
            "tables": proc.stdout,
            "ok": proc.returncode == 0,
            "error": proc.stderr.strip() if proc.returncode else None,
        }
        self._eval_cache = (stamp, result)
        return result


DASHBOARD_HTML = """<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Neural Amplifier // Planetary Datalinks</title><style>
:root{color-scheme:dark;--bg:#02070d;--panel:#071b28;--edge:#39c9d2;--text:#b8f5ef;--muted:#689aa0;--gold:#f1d56a;--bad:#ff6262}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 50% 0,#0b3142,#02070d 55%);color:var(--text);font:14px/1.45 "Lucida Console",Monaco,monospace}header{padding:18px 24px;border-bottom:2px ridge var(--edge);letter-spacing:.15em;background:#031019}h1{margin:0;color:#7ffff5;font-size:20px}.status{color:var(--gold)}main{padding:18px;display:grid;gap:16px}.panel{background:linear-gradient(145deg,#0a2634,#04121b);border:3px ridge #287f8b;box-shadow:0 0 16px #001 inset;padding:14px}h2{font-size:15px;color:#5fe8f0;border-bottom:1px solid #287f8b;padding-bottom:7px;margin:0 0 12px}.summary,.factions{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.datum,.faction{border:1px solid #1b6873;padding:8px}.datum b{display:block;color:#fff;font-size:18px}.faction{border-left:7px solid var(--faction)}.faction h3{margin:0 0 8px;color:#fff}.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:4px}.stats span{color:var(--muted)}.stats b{color:var(--text);float:right}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px;border-bottom:1px solid #164650}th{color:#64dfe6}.decision{cursor:pointer}.decision:hover{background:#123b48}.bad{color:var(--bad)}.tabs button{background:#092734;color:var(--text);border:2px ridge #287f8b;padding:8px 14px;cursor:pointer}.hidden{display:none}pre{white-space:pre-wrap;word-break:break-word;color:#c8f7f3;max-height:70vh;overflow:auto}.detail{position:fixed;inset:5%;z-index:2;overflow:auto}.close{float:right}.why{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.why section{border:1px solid #1b6873;padding:10px}.why h3{color:var(--gold);margin:0 0 8px}.why .wide{grid-column:1/-1}.why ul{margin:0;padding-left:20px}.disagreement{border-color:var(--bad)!important;color:#ffb0b0}.dstate{display:inline-block;padding:1px 7px;margin-left:6px;border:1px solid currentColor;font-size:11px;letter-spacing:.08em}.d-followed{color:#55e06f}.d-overrode{color:var(--gold)}.d-unsatisfied{color:#ff9f43}.d-unmeasurable,.d-rejected,.d-conflicts{color:var(--muted)}.d-none{color:var(--muted)}.gs{padding:2px 8px;border:1px solid currentColor;letter-spacing:.08em}.gs-absent,.gs-empty{color:var(--muted)}.gs-degraded{color:var(--bad)}.gs-present{color:#55e06f}.cited{color:#7ffff5}.uncited{color:var(--muted)}.banner{padding:12px 16px;margin:0 0 4px;border:3px ridge #287f8b;background:#071b28}.banner b{display:block;font-size:17px;letter-spacing:.1em}.banner.paused{border-color:var(--gold);color:var(--gold)}.banner.live{border-color:#55e06f;color:#9ef7b5}.banner.no-run{border-color:var(--bad);color:#ffb0b0}.banner span{color:var(--muted)}abbr{text-decoration:underline dotted;cursor:help}@media(max-width:700px){main{padding:8px}.panel{overflow:auto}}
</style></head><body><header><h1>NEURAL AMPLIFIER // PLANETARY DATALINKS</h1><span id=status class=status>LINKING…</span></header><main>
<div id=banner class=banner><b>LINKING…</b></div>
<section class=panel><h2>MISSION CONTROL</h2><div id=summary class=summary></div></section>
<section class=panel><h2>FACTION STATUS</h2><div id=factions class=factions></div></section>
<section class=panel><h2>DECISION ARCHIVE</h2><table><thead><tr><th>Turn<th>Faction<th>What was decided<th><abbr title="Who actually chose: the language model, or the game engine's own default when the model could not be reached.">Decided by</abbr><th>Action taken<th><abbr title="What the game engine would have picked on its own, for comparison.">Engine would pick</abbr><th>Took<th>Cost</tr></thead><tbody id=decisions></tbody></table></section>
<section class=panel><h2>KNOWLEDGE GRAPH</h2><input id=gq placeholder="search the graph (e.g. colony pod)" style="width:60%;background:#092734;color:var(--text);border:2px ridge #287f8b;padding:7px"> <button onclick="loadGraph(gq.value)">SEARCH</button> <button onclick="loadGraph('')">CENSUS</button><div id=graph>Loading graph…</div></section>
<section class=panel><h2>STRATEGY IN FORCE</h2><div id=strategy>Loading directives…</div></section>
<section class=panel><h2>EVALUATION DATALINKS</h2><button onclick=loadEvals()>RE-DERIVE COMMITTED TABLES</button><div id=evals class=why>Loading committed scorers…</div></section></main>
<section id=detail class="panel detail hidden"><button class=close onclick="detail.classList.add('hidden')">CLOSE</button><h2>DECISION DATALINK</h2><div id=detailText class=why></div></section>
<script>
//: `status` is NOT the element. `window.status` is a legacy string property, so the bare
//: identifier resolves to it and `statusEl.textContent=...` writes to a throwaway String wrapper
//: and is silently discarded — every other id here (summary, factions, decisions, evals, detail,
//: detailText) resolves to its element, and only this one collides. Measured in a real browser:
//: `(0,eval)('status')` returns "" (a string), not the span. The banner could therefore never
//: leave "LINKING…", and — the part that cost real time — the catch block's own error text
//: could never be shown either, so the page had no way to report its own failure. na-uq1.
//: (The wording of that catch block has since changed; it is quoted here only as history, so
//: this comment deliberately does not reproduce the old string — the page is now checked for
//: internal jargon by substring, and a comment quoting the banned text would fail that guard
//: for no reason.)
const statusEl=document.getElementById('status');
let GLOSS={};
async function loadGlossary(){try{GLOSS=await fetch('/dashboard/api/glossary').then(r=>r.json())}catch(e){GLOSS={}}}
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function refresh(){let delay=30000;try{const [l,d]=await Promise.all([fetch('/dashboard/api/live').then(r=>r.json()),fetch('/dashboard/api/decisions?limit=100').then(r=>r.json())]);delay=l.active?5000:30000;const rs=l.run_state??{state:'paused',headline:'UNKNOWN',detail:''};statusEl.textContent=rs.headline;banner.className='banner '+rs.state;banner.innerHTML=`<b>${esc(rs.headline)}</b><span>${esc(rs.detail)}</span>`;summary.innerHTML=[['GAME',l.game_id],['TURN',l.turn],['DECISIONS',l.decisions],['SPEND USD',l.spend],['RUN',l.run_id],['ARM',l.arm],['SEED',l.seed],['DIFFICULTY',l.fairness?.difficulty],['SLOT',l.fairness?.slot],['VICTORY',l.victory]].map(x=>`<div class=datum>${esc(x[0])}<b>${esc(x[1])}</b></div>`).join('');factions.innerHTML=l.factions.map(f=>`<article class=faction style="--faction:${esc(f.colour)}"><h3>${esc(f.name)}</h3><div class=stats>${[['BASES',f.bases],['POP',f.population],['MINERALS',f.minerals],['ENERGY',f.energy],['INCOME',f.income],['LABS',f.labs],['MILITARY',f.military],['TECHS',f.techs]].map(x=>`<span>${esc(x[0])}<b>${esc(x[1])}</b></span>`).join('')}</div></article>`).join('');decisions.innerHTML=d.map(x=>`<tr class="decision ${x.disagreed?'bad':''}" onclick="showDecision(${x.id})"><td>${esc(x.turn)}<td>${esc(x.faction)}<td>${esc(x.surface)}<td>${x.degraded?`<abbr title="${esc(GLOSS.fallback?.help??'')}">${esc(GLOSS.fallback?.name??'Fallback')}</abbr>`:`<abbr title="${esc(GLOSS.tier?.[x.tier]?.help??'')}">${esc(GLOSS.tier?.[x.tier]?.name??x.tier)}</abbr>`}<td>${esc(x.chosen.join(', '))}<td>${esc(x.native)}<td>${esc(x.latency_ms)} ms<td>${esc(x.cost)}</tr>`).join('')}catch(e){statusEl.textContent='CANNOT REACH THE DASHBOARD SERVICE';banner.className='banner no-run';banner.innerHTML=`<b>CANNOT REACH THE DASHBOARD SERVICE</b><span>The page is up but its data service did not answer: ${esc(e)}</span>`}finally{setTimeout(refresh,delay)}}
//: Each empty state gets its OWN sentence. "No facts" for all three would erase the
//: distinction the orchestrator went to the trouble of recording (quipu_absent vs
//: quipu_degraded vs a real query that matched nothing) - and the degraded case is a
//: FAULT that would then read as a quiet, healthy nothing.
const GROUNDING_NOTE={absent:'No retriever was configured for this run, so nothing was ever asked. This is not "the graph had nothing to say".',degraded:'Retrieval was configured and FAILED for this decision. The brain decided without grounding it should have had.',empty:'Retrieval ran and matched nothing. The graph genuinely had no fact for this surface.',present:''};
const list=x=>(x??[]).map(v=>`<li>${esc(typeof v==='object'?JSON.stringify(v):v)}</li>`).join('')||'<li>—</li>';
async function showDecision(id){const x=await fetch('/dashboard/api/decisions/'+id).then(r=>r.json()),r=x.record,p=r.plan??{},w=x.why??{directives:[],grounding:{state:'empty',label:'',facts:[]},guard:{}},chosen=(r.chosen??[]).map(c=>c.action_id??c.id),native=x.native_choice,disagreed=native!=null&&!chosen.map(String).includes(String(native));detailText.innerHTML=`<section><h3>CONTEXT</h3><b>TURN ${esc(r.turn)} // ${esc(r.faction)}</b><p>${esc(r.surface_id)}</p><p>Tier: ${esc(r.tier)} // Applied: ${esc(chosen.join(', '))}</p><p>Degraded: ${esc(r.degraded)} ${esc(r.degrade_reason??r.fallback_reason??'')}</p></section><section class="${disagreed?'disagreement':''}"><h3>CHOICE ${disagreed?'// DISAGREEMENT':''}</h3><p>Chosen: ${esc(chosen.join(', '))}</p><p>Native: ${esc(native)}</p></section><section class=wide><h3>WHY</h3><p>${esc(r.reason)}</p></section><section class=wide><h3>OFFERED ACTION SPACE</h3><table><thead><tr><th>Action<th>Cost<th>Turns<th>Effects</tr></thead><tbody>${(x.action_space??[]).map(a=>`<tr><td>${esc(a.action??a.name??a.id)}<td>${esc(a.cost)} ${esc(a.cost_unit??'')}<td>${esc(a.turns??a.turns_to_completion)}<td>${esc(a.effects??a.board_effects)}</tr>`).join('')}</tbody></table></section><section class=wide><h3>DIRECTIVES IN FORCE</h3>${w.plan_absent?'<p class=d-none>NO PLAN IN FORCE FOR THIS DECISION</p>':(w.directives??[]).length?`<table><thead><tr><th>Directive<th>Disposition</tr></thead><tbody>${(w.directives??[]).map(d=>`<tr><td>${esc(d.id)}<td>${d.dispositions.length?d.dispositions.map(k=>`<span class="dstate d-${esc(k)}" title="${esc(GLOSS.disposition?.[k]?.help??'')}">${esc((GLOSS.disposition?.[k]?.name??k).toUpperCase())}</span>`).join(''):'<span class="dstate d-none">IN FORCE // NO DISPOSITION RECORDED</span>'}</tr>`).join('')}</tbody></table>`:'<p class=d-none>NONE</p>'}</section><section class=wide><h3>FACTS THE MODEL WAS GIVEN <abbr class="gs gs-${esc(w.grounding.state)}" title="${esc(GLOSS.grounding?.[w.grounding.state]?.help??'')}">${esc(GLOSS.grounding?.[w.grounding.state]?.name??w.grounding.label)}</abbr></h3>${w.grounding.facts.length?`<ul>${w.grounding.facts.map(f=>`<li class=${f.cited?'cited':'uncited'}>${f.cited?'[CITED] ':''}${esc(f.text)}</li>`).join('')}</ul>`:`<p class=d-none>${esc(GROUNDING_NOTE[w.grounding.state]??'')}</p>`}</section><section><h3>SAFETY CHECKS</h3><p><abbr title="Whether the safety checker allowed this action. It can block a choice before the game sees it.">Result</abbr>: ${esc(w.guard.verdict==='allow'?'Allowed':(w.guard.verdict??'—'))}${w.guard.absent?' — checker not connected':''}${w.guard.degraded?' — the checker failed to answer':''}</p><b><abbr title="Warnings the checker added to what the model was shown.">Warnings given to the model</abbr></b><ul>${list(w.guard.advisories)}</ul><b><abbr title="Information removed before the model saw it, so it could not act on knowledge this faction should not have.">Hidden from the model</abbr></b><ul>${list(w.guard.stripped)}</ul></section><section><h3>TELEMETRY</h3><p>Latency: ${esc(r.latency_ms)} ms</p><p>Cost: $${esc(r.cost_usd??r.usd)}</p><p>Model: ${esc(r.model)}</p></section>`;detail.classList.remove('hidden')}
function renderEvals(x){const labels={};for(const run of x.runs??[])labels[run.id]=[...(run.tables??[])];const sections=x.tables.trim().split(/\\n(?==== )/).filter(s=>s.startsWith('=== '));evals.innerHTML=sections.map(section=>{const lines=section.split('\\n'),head=lines.shift().replace(/^=== | ===$/g,''),run=head.split(':')[0].trim(),label=(labels[run]??[]).shift()??head,rows=[];for(const line of lines){const m=line.match(/^(\\S+)\\s+(-?\\d+)\\s+(-?\\d+)\\s+([+-]?\\d+)$/);if(m)rows.push(m.slice(1))}const table=rows.length?`<table><thead><tr><th>Metric<th>Baseline<th>Arm<th>Delta</tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r[0])}<td>${esc(r[1])}<td>${esc(r[2])}<td>${esc(r[3])}</tr>`).join('')}</tbody></table>`:'';const prose=lines.filter(line=>!rows.some(r=>line.trim().startsWith(r[0]))&& !line.trim().startsWith('metric ')).join('\\n').trim();return `<section class=wide><h3>${esc(run)} // ${esc(label)}</h3>${table}<pre>${esc(prose)}</pre></section>`}).join('')+(x.error?`<section class="wide bad"><h3>SCORER ERROR</h3>${esc(x.error)}</section>`:'')}
async function loadGraph(q){const u=q?('/dashboard/api/graph?q='+encodeURIComponent(q)):'/dashboard/api/graph';const x=await fetch(u).then(r=>r.json());
//: Each non-ok state gets its own sentence. An unconfigured graph and an unreachable one are
//: the operator's problem; an empty result is the graph's. One blank list for all three would
//: hide a broken link behind "no results".
if(x.state!=='ok'){graph.innerHTML=`<p class="gs gs-${x.state==='empty'?'empty':'degraded'}">${esc(x.state.toUpperCase())}</p><p class=d-none>${esc(x.detail)}</p>`;return}
if(x.mode==='census'){graph.innerHTML=`<table><thead><tr><th>Type<th>Entities</tr></thead><tbody>${x.rows.map(r=>`<tr><td>${esc(r.type)}<td>${esc(r.count)}</tr>`).join('')}</tbody></table>`;return}
if(x.mode==='entity'){graph.innerHTML=`<table><thead><tr><th>Predicate<th>Value</tr></thead><tbody>${x.rows.map(r=>`<tr><td>${esc(String(r.predicate).split(/[#/]/).pop())}<td>${esc(r.object)}</tr>`).join('')}</tbody></table>`;return}
graph.innerHTML=`<table><thead><tr><th>Entity<th>Score<th>Text</tr></thead><tbody>${x.rows.map(r=>`<tr class=decision onclick="showEntity('${esc(r.entity)}')"><td>${esc(String(r.entity).split('/').pop())}<td>${esc(Number(r.score).toFixed(3))}<td>${esc(String(r.text).slice(0,160))}</tr>`).join('')}</tbody></table>`}
async function showEntity(iri){const x=await fetch('/dashboard/api/graph?entity='+encodeURIComponent(iri)).then(r=>r.json());graph.innerHTML=`<button onclick="loadGraph(gq.value)">BACK</button><h3>${esc(iri.split('/').pop())}</h3>`+(x.state!=='ok'?`<p class=d-none>${esc(x.detail)}</p>`:`<table><thead><tr><th>Predicate<th>Value</tr></thead><tbody>${x.rows.map(r=>`<tr><td>${esc(String(r.predicate).split(/[#/]/).pop())}<td>${esc(r.object)}</tr>`).join('')}</tbody></table>`)}
async function loadStrategy(){const x=await fetch('/dashboard/api/strategy').then(r=>r.json());const defs=x.definitions??{};
//: An id is not a directive. When no plan file is wired the panel says so, rather than
//: listing bare ids as if that were the whole strategy — same rule as the grounding badges.
const note=(x.definitions_missing??[]).length?`<p class=d-none>Directive TEXT shown for ${esc(x.definitions_covered)} of ${esc(x.directive_count)} (source: ${esc(x.definitions_source)}). The rest were issued at runtime and their text is not in any committed artifact — the dispositions below are still complete.</p>`:'';
const totals=(x.totals??[]).map(t=>{const def=defs[t.id]??{};return `<tr><td>${esc(t.id)}${def.intent?`<br><span class=uncited>${esc(def.intent)}</span>`:''}<td>${esc(def.metric)} ${esc(def.comparator)} ${esc(def.target)}<td>${esc(t.in_force)}<td>${esc(t.followed??0)}<td>${esc(t.overrode??0)}<td>${esc(t.unsatisfied??0)}<td>${esc(t.unmeasurable??0)}<td>${t.followed_share==null?'—':esc(t.followed_share)}</tr>`}).join('');
strategy.innerHTML=note+(totals?`<table><thead><tr><th>Directive<th>Metric<th>Turns in force<th>Followed<th>Overrode<th>Unsatisfied<th>Unmeasurable<th>Followed share</tr></thead><tbody>${totals}</tbody></table>`:'<p class=d-none>No directives were in force in this run.</p>')}
async function loadEvals(){evals.textContent='Re-deriving…';const x=await fetch('/dashboard/api/evals').then(r=>r.json());renderEvals(x)}
loadGlossary().then(()=>{refresh();loadEvals();loadStrategy();loadGraph('')});
</script></body></html>"""
