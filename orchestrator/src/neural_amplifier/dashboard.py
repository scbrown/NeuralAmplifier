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
        return {
            "record": record.model_dump(mode="json"),
            "world_view": view,
            "action_space": (view or {}).get("action_space", []),
            "native_choice": (view or {}).get("native_choice"),
            "native_choice_name": (view or {}).get("native_choice_name"),
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
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 50% 0,#0b3142,#02070d 55%);color:var(--text);font:14px/1.45 "Lucida Console",Monaco,monospace}header{padding:18px 24px;border-bottom:2px ridge var(--edge);letter-spacing:.15em;background:#031019}h1{margin:0;color:#7ffff5;font-size:20px}.status{color:var(--gold)}main{padding:18px;display:grid;gap:16px}.panel{background:linear-gradient(145deg,#0a2634,#04121b);border:3px ridge #287f8b;box-shadow:0 0 16px #001 inset;padding:14px}h2{font-size:15px;color:#5fe8f0;border-bottom:1px solid #287f8b;padding-bottom:7px;margin:0 0 12px}.summary,.factions{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.datum,.faction{border:1px solid #1b6873;padding:8px}.datum b{display:block;color:#fff;font-size:18px}.faction{border-left:7px solid var(--faction)}.faction h3{margin:0 0 8px;color:#fff}.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:4px}.stats span{color:var(--muted)}.stats b{color:var(--text);float:right}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px;border-bottom:1px solid #164650}th{color:#64dfe6}.decision{cursor:pointer}.decision:hover{background:#123b48}.bad{color:var(--bad)}.tabs button{background:#092734;color:var(--text);border:2px ridge #287f8b;padding:8px 14px;cursor:pointer}.hidden{display:none}pre{white-space:pre-wrap;word-break:break-word;color:#c8f7f3;max-height:70vh;overflow:auto}.detail{position:fixed;inset:5%;z-index:2;overflow:auto}.close{float:right}.why{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.why section{border:1px solid #1b6873;padding:10px}.why h3{color:var(--gold);margin:0 0 8px}.why .wide{grid-column:1/-1}.why ul{margin:0;padding-left:20px}.disagreement{border-color:var(--bad)!important;color:#ffb0b0}@media(max-width:700px){main{padding:8px}.panel{overflow:auto}}
</style></head><body><header><h1>NEURAL AMPLIFIER // PLANETARY DATALINKS</h1><span id=status class=status>LINKING…</span></header><main>
<section class=panel><h2>MISSION CONTROL</h2><div id=summary class=summary></div></section>
<section class=panel><h2>FACTION STATUS</h2><div id=factions class=factions></div></section>
<section class=panel><h2>DECISION ARCHIVE</h2><table><thead><tr><th>Turn<th>Faction<th>Surface<th>Tier<th>Choice<th>Native<th>Latency<th>Cost</tr></thead><tbody id=decisions></tbody></table></section>
<section class=panel><h2>EVALUATION DATALINKS</h2><button onclick=loadEvals()>RE-DERIVE COMMITTED TABLES</button><pre id=evals>Scorers run only on request and are cached.</pre></section></main>
<section id=detail class="panel detail hidden"><button class=close onclick="detail.classList.add('hidden')">CLOSE</button><h2>DECISION DATALINK</h2><div id=detailText class=why></div></section>
<script>
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function refresh(){let delay=30000;try{const [l,d]=await Promise.all([fetch('/dashboard/api/live').then(r=>r.json()),fetch('/dashboard/api/decisions?limit=100').then(r=>r.json())]);delay=l.active?5000:30000;status.textContent=!l.configured?'NO RUN ARTIFACTS CONFIGURED':l.active?`LIVE // TURN ${l.turn??'—'} // ${l.decisions} DECISIONS`:`IDLE SINCE ${l.updated_at?new Date(l.updated_at).toLocaleString():'UNKNOWN'}`;summary.innerHTML=[['GAME',l.game_id],['TURN',l.turn],['DECISIONS',l.decisions],['SPEND USD',l.spend],['RUN',l.run_id],['ARM',l.arm],['SEED',l.seed],['DIFFICULTY',l.fairness?.difficulty],['SLOT',l.fairness?.slot],['VICTORY',l.victory]].map(x=>`<div class=datum>${esc(x[0])}<b>${esc(x[1])}</b></div>`).join('');factions.innerHTML=l.factions.map(f=>`<article class=faction style="--faction:${esc(f.colour)}"><h3>${esc(f.name)}</h3><div class=stats>${[['BASES',f.bases],['POP',f.population],['MINERALS',f.minerals],['ENERGY',f.energy],['INCOME',f.income],['LABS',f.labs],['MILITARY',f.military],['TECHS',f.techs]].map(x=>`<span>${esc(x[0])}<b>${esc(x[1])}</b></span>`).join('')}</div></article>`).join('');decisions.innerHTML=d.map(x=>`<tr class="decision ${x.disagreed?'bad':''}" onclick="showDecision(${x.id})"><td>${esc(x.turn)}<td>${esc(x.faction)}<td>${esc(x.surface)}<td>${esc(x.degraded?'DEGRADED':x.tier)}<td>${esc(x.chosen.join(', '))}<td>${esc(x.native)}<td>${esc(x.latency_ms)} ms<td>${esc(x.cost)}</tr>`).join('')}catch(e){status.textContent='LINK DEGRADED // '+e}finally{setTimeout(refresh,delay)}}
const list=x=>(x??[]).map(v=>`<li>${esc(typeof v==='object'?JSON.stringify(v):v)}</li>`).join('')||'<li>—</li>';
async function showDecision(id){const x=await fetch('/dashboard/api/decisions/'+id).then(r=>r.json()),r=x.record,p=r.plan??{},chosen=(r.chosen??[]).map(c=>c.action_id??c.id),native=x.native_choice,disagreed=native!=null&&!chosen.map(String).includes(String(native));detailText.innerHTML=`<section><h3>CONTEXT</h3><b>TURN ${esc(r.turn)} // ${esc(r.faction)}</b><p>${esc(r.surface_id)}</p><p>Tier: ${esc(r.tier)} // Applied: ${esc(chosen.join(', '))}</p><p>Degraded: ${esc(r.degraded)} ${esc(r.degrade_reason??r.fallback_reason??'')}</p></section><section class="${disagreed?'disagreement':''}"><h3>CHOICE ${disagreed?'// DISAGREEMENT':''}</h3><p>Chosen: ${esc(chosen.join(', '))}</p><p>Native: ${esc(native)}</p></section><section class=wide><h3>WHY</h3><p>${esc(r.reason)}</p></section><section class=wide><h3>OFFERED ACTION SPACE</h3><table><thead><tr><th>Action<th>Cost<th>Turns<th>Effects</tr></thead><tbody>${(x.action_space??[]).map(a=>`<tr><td>${esc(a.action??a.name??a.id)}<td>${esc(a.cost)} ${esc(a.cost_unit??'')}<td>${esc(a.turns??a.turns_to_completion)}<td>${esc(a.effects??a.board_effects)}</tr>`).join('')}</tbody></table></section><section><h3>PLAN DIRECTIVES</h3><b>IN FORCE</b><ul>${list(p.in_force)}</ul><b>FOLLOWED</b><ul>${list(p.followed)}</ul><b>OVERRODE</b><ul>${list(p.overrode)}</ul></section><section><h3>TELEMETRY</h3><p>Latency: ${esc(r.latency_ms)} ms</p><p>Cost: $${esc(r.cost_usd??r.usd)}</p><p>Model: ${esc(r.model)}</p></section>`;detail.classList.remove('hidden')}
async function loadEvals(){evals.textContent='Re-deriving…';const x=await fetch('/dashboard/api/evals').then(r=>r.json());evals.textContent=x.tables+(x.error?'\n'+x.error:'')}
refresh();
</script></body></html>"""
