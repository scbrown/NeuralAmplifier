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
from pathlib import Path
from typing import Any

from .decisions import DecisionLog, DecisionRecord
from .replay import WorldViewStore

REPO = Path(__file__).resolve().parents[3]
FACTION_COLOURS = ("#62d6ff", "#ff4545", "#f4e04d", "#55e06f", "#c880ff", "#ff9f43", "#eeeeee")


def _choice_id(choice: dict[str, Any]) -> str:
    return str(choice.get("action_id", choice.get("id", "—")))


def _record_cost(record: DecisionRecord) -> float | None:
    """Model spend when a producer recorded it; never confuse it with game energy credits."""
    extra = record.model_extra or {}
    raw = extra.get("cost_usd", extra.get("usd"))
    return float(raw) if isinstance(raw, int | float) else None


class DashboardReader:
    """Small, bounded projections over an append-only run."""

    def __init__(self, log: DecisionLog | None, store: WorldViewStore | None) -> None:
        self.log = log
        self.store = store
        self._eval_cache: tuple[int, dict[str, Any]] | None = None

    def records(self) -> list[DecisionRecord]:
        return list(self.log.read()) if self.log is not None else []

    def world_view(self, record: DecisionRecord) -> dict[str, Any] | None:
        view = self.store.get(record.world_view_hash) if self.store is not None else None
        return view.model_dump(mode="json") if view is not None else None

    def live(self) -> dict[str, Any]:
        records = self.records()
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

        factions = []
        for index, (name, (record, view)) in enumerate(sorted(latest.items())):
            raw_metrics = view.get("metrics")
            metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
            factions.append(
                {
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
            )

        newest = max(records, key=lambda r: r.turn, default=None)
        newest_view = self.world_view(newest) if newest is not None else None
        fairness = (newest_view or {}).get("fairness") or (
            {"handicaps": newest.fairness_profile} if newest is not None else {}
        )
        return {
            "configured": self.log is not None,
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
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 50% 0,#0b3142,#02070d 55%);color:var(--text);font:14px/1.45 "Lucida Console",Monaco,monospace}header{padding:18px 24px;border-bottom:2px ridge var(--edge);letter-spacing:.15em;background:#031019}h1{margin:0;color:#7ffff5;font-size:20px}.status{color:var(--gold)}main{padding:18px;display:grid;gap:16px}.panel{background:linear-gradient(145deg,#0a2634,#04121b);border:3px ridge #287f8b;box-shadow:0 0 16px #001 inset;padding:14px}h2{font-size:15px;color:#5fe8f0;border-bottom:1px solid #287f8b;padding-bottom:7px;margin:0 0 12px}.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}.datum{border:1px solid #1b6873;padding:8px}.datum b{display:block;color:#fff;font-size:18px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px;border-bottom:1px solid #164650}th{color:#64dfe6}.decision{cursor:pointer}.decision:hover{background:#123b48}.bad{color:var(--bad)}.tabs button{background:#092734;color:var(--text);border:2px ridge #287f8b;padding:8px 14px;cursor:pointer}.hidden{display:none}pre{white-space:pre-wrap;word-break:break-word;color:#c8f7f3;max-height:70vh;overflow:auto}.detail{position:fixed;inset:5%;z-index:2;overflow:auto}.close{float:right}@media(max-width:700px){main{padding:8px}.panel{overflow:auto}}
</style></head><body><header><h1>NEURAL AMPLIFIER // PLANETARY DATALINKS</h1><span id=status class=status>LINKING…</span></header><main>
<section class=panel><h2>MISSION CONTROL</h2><div id=summary class=summary></div></section>
<section class=panel><h2>FACTION STATUS</h2><table><thead><tr><th>Faction<th>Bases<th>Population<th>Minerals<th>Energy<th>Income<th>Labs<th>Military<th>Techs</tr></thead><tbody id=factions></tbody></table></section>
<section class=panel><h2>DECISION ARCHIVE</h2><table><thead><tr><th>Turn<th>Faction<th>Surface<th>Tier<th>Choice<th>Native<th>Latency<th>Cost</tr></thead><tbody id=decisions></tbody></table></section>
<section class=panel><h2>EVALUATION DATALINKS</h2><button onclick=loadEvals()>RE-DERIVE COMMITTED TABLES</button><pre id=evals>Scorers run only on request and are cached.</pre></section></main>
<section id=detail class="panel detail hidden"><button class=close onclick="detail.classList.add('hidden')">CLOSE</button><h2>DECISION DATALINK</h2><pre id=detailText></pre></section>
<script>
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function refresh(){try{const [l,d]=await Promise.all([fetch('/dashboard/api/live').then(r=>r.json()),fetch('/dashboard/api/decisions?limit=100').then(r=>r.json())]);status.textContent=l.configured?`LIVE // TURN ${l.turn??'—'} // ${l.decisions} DECISIONS`:'NO RUN ARTIFACTS CONFIGURED';summary.innerHTML=[['TURN',l.turn],['DECISIONS',l.decisions],['SPEND',l.spend],['RUN',l.run_id],['ARM',l.arm],['SEED',l.seed],['VICTORY',l.victory],['FAIRNESS',JSON.stringify(l.fairness)]].map(x=>`<div class=datum>${esc(x[0])}<b>${esc(x[1])}</b></div>`).join('');factions.innerHTML=l.factions.map(f=>`<tr style="border-left:5px solid ${esc(f.colour)}"><td>${esc(f.name)}<td>${esc(f.bases)}<td>${esc(f.population)}<td>${esc(f.minerals)}<td>${esc(f.energy)}<td>${esc(f.income)}<td>${esc(f.labs)}<td>${esc(f.military)}<td>${esc(f.techs)}</tr>`).join('');decisions.innerHTML=d.map(x=>`<tr class="decision ${x.disagreed?'bad':''}" onclick="showDecision(${x.id})"><td>${esc(x.turn)}<td>${esc(x.faction)}<td>${esc(x.surface)}<td>${esc(x.degraded?'DEGRADED':x.tier)}<td>${esc(x.chosen.join(', '))}<td>${esc(x.native)}<td>${esc(x.latency_ms)} ms<td>${esc(x.cost)}</tr>`).join('')}catch(e){status.textContent='LINK DEGRADED // '+e}}
async function showDecision(id){const x=await fetch('/dashboard/api/decisions/'+id).then(r=>r.json());detailText.textContent=JSON.stringify(x,null,2);detail.classList.remove('hidden')}
async function loadEvals(){evals.textContent='Re-deriving…';const x=await fetch('/dashboard/api/evals').then(r=>r.json());evals.textContent=x.tables+(x.error?'\n'+x.error:'')}
refresh();setInterval(refresh,5000);
</script></body></html>"""
