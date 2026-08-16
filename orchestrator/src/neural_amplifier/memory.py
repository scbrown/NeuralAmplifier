"""Learned memory — K3, ``docs/knowledge-architecture.md`` §"The learned-memory model".

The gap this closes: a game's decisions are written to a JSONL, read once by whoever is looking,
and then they may as well not have happened. Nothing a run *learned* reaches the next one. The
exit criterion the roadmap sets is exactly that and nothing softer — **a tactic learned in game N
surfaces in game N+1** — so this module is judged by whether recall works, not by whether the
write looks tidy.

Three parts, and they are deliberately separable:

``episodes()``    a decision log → ``mem:`` nodes and edges. Pure, no network, no store.
``write()``       posts them through ``quipu_episode``, which is what buys provenance
                  (``prov:wasGeneratedBy``) and entity resolution for free.
``recall()``      reads tactics back for the next game and hands them to the prompt.

**Written through episodes, never as free Turtle.** The architecture is explicit about this and
the reason is provenance: an episode gets ``prov:wasGeneratedBy`` automatically, so a tactic can
always be traced to the game that produced it. Hand-written Turtle would put the burden on
whoever remembered.

**The bitemporal split is the whole design, not decoration.** Per-game episodes go to
``memory:game:<id>`` where valid-time is the in-game turn, so "what did I know at turn 30 of
*this* game" is answerable and the whole group can be archived when the game ends. Durable
tactics go to ``memory:durable`` on wall-clock time, because their claim is about SMAC rather
than about one match. Collapsing them would make a lesson from one game indistinguishable from a
lesson from forty, which is the difference between a tactic and an anecdote.

**A tactic is a claim, so it carries confidence and can be refuted.** ``mem:Outcome`` edges
(``confirmedBy`` / ``refutedBy``) are what let a belief weaken, and a memory that can only
accumulate is one that gets more confident as it gets more wrong.

**Recall degrades, never stalls.** Same rule as every other knowledge path (``knowledge.py``): a
missing or unreachable store means an unaugmented prompt, not a failed turn.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .contract import WorldView
from .datalinks.quipu import QuipuRetriever
from .decisions import DecisionRecord
from .knowledge import Grounding

#: Prefix on every node NAME this module writes, so learned memory is distinguishable from any
#: other plane sharing the store. Names only — quipu refuses a prefixed node TYPE outright,
#: rather than silently rewriting `mem:Tactic` to `mem_Tactic`, which is the right refusal and
#: is why the types below are bare.
MEM = "mem:"

#: The namespace quipu mints node and predicate IRIs into. A recall query has to name this
#: exactly, and it is quipu's to choose rather than ours — discovered by writing an episode and
#: reading the triples back, not assumed.
MEM_IRI = "http://aegis.gastown.local/ontology/"


#: Per-game memories: valid-time is the in-game turn, and the group is archived at game end.
def game_group(game_id: str) -> str:
    return f"memory:game:{game_id}"


#: Durable memories: wall-clock valid-time, confidence rising and falling across games.
DURABLE_GROUP = "memory:durable"

#: How many times a surface must resolve the same way before it is worth calling a tactic.
#: Two is a coincidence; three is a habit. Deliberately low, because the confidence attached to
#: the claim is what carries the doubt — a tactic seen three times is *recorded* at low
#: confidence rather than withheld, and `mem:Outcome` is how it earns or loses ground later.
TACTIC_THRESHOLD = 3


@dataclass
class Extraction:
    """What one game's log yielded."""

    game_id: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    #: Durable tactics, kept apart from the per-game nodes because they go to a different group
    #: on a different time axis. One list would have to be re-split at write time by inspecting
    #: node types, which is the kind of implicit routing that ends up half-right.
    tactics: list[dict[str, Any]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.nodes or self.tactics)


def _decisions(log: Path) -> list[DecisionRecord]:
    """Every well-formed record in a decision log.

    A truncated final line is expected rather than exceptional — the log is appended as the game
    runs and a run killed mid-write leaves one. Skipping it costs that decision; raising would
    cost the whole game's memory, which is the trade this module exists to refuse.
    """
    out: list[DecisionRecord] = []
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(DecisionRecord.model_validate(json.loads(line)))
        except Exception:
            continue
    return out


def episodes(log: Path) -> Extraction:
    """A game's decision log as ``mem:`` nodes and edges.

    Only LLM-tier decisions become tactics. A deterministic-tier record says what the engine
    would have done anyway, so learning from it would teach the brain the fallback's habits and
    then report the agreement as evidence the brain was right.

    Degraded decisions are excluded for a sharper reason: the record exists precisely because the
    brain could not answer, so the chosen action is the fallback's. Counting it would build a
    tactic out of the moments the brain was absent.
    """
    records = _decisions(log)
    if not records:
        return Extraction(game_id="")

    game_id = records[0].game_id or "unknown"
    out = Extraction(game_id=game_id)

    out.nodes.append(
        {
            "name": f"{MEM}game/{game_id}",
            "type": "Game",
            "description": f"{records[0].engine} as {records[0].faction}",
            "properties": {
                "engine": records[0].engine,
                "faction": records[0].faction,
                "decisions": len(records),
                "last_turn": max(r.turn for r in records),
            },
        }
    )

    considered = [r for r in records if r.tier == "llm" and not r.degraded and r.chosen]
    for record in considered:
        name = f"{MEM}decision/{game_id}/{record.turn}/{record.surface_id or 'unstamped'}"
        chosen = [str(c.get("action_id")) for c in record.chosen]
        out.nodes.append(
            {
                "name": name,
                "type": "Decision",
                "description": record.reason or "",
                "properties": {
                    "turn": record.turn,
                    "surface_id": record.surface_id or "",
                    "chosen": ", ".join(chosen),
                    "faction": record.faction,
                },
            }
        )
        out.edges.append({"source": name, "target": f"{MEM}game/{game_id}", "relation": "inGame"})

    out.tactics = _tactics(game_id, considered)
    return out


def _tactics(game_id: str, records: list[DecisionRecord]) -> list[dict[str, Any]]:
    """Repeated choices on a surface, as durable claims.

    A tactic here is the smallest honest generalisation the log actually supports: *on this
    surface, this faction repeatedly chose this*. It deliberately does not claim the choice was
    good — nothing in a decision log knows that. ``mem:Outcome`` is where a tactic earns or loses
    confidence once outcomes are wired (na-6db), and until then a tactic is a recorded habit
    rather than an endorsed one, which is what the confidence number is saying.
    """
    seen: Counter[tuple[str, str]] = Counter()
    for record in records:
        surface = record.surface_id or ""
        if not surface:
            continue
        for choice in record.chosen:
            seen[(surface, str(choice.get("action_id")))] += 1

    out: list[dict[str, Any]] = []
    for (surface, action), count in sorted(seen.items()):
        if count < TACTIC_THRESHOLD:
            continue
        out.append(
            {
                "name": f"{MEM}tactic/{surface}/{action}",
                "type": "Tactic",
                "description": f"On {surface}, prefer {action}.",
                "properties": {
                    "trigger": surface,
                    "action": action,
                    "observations": count,
                    # Rises with evidence and is capped well below certainty: a habit observed
                    # in one game is not a law of the game, however many times it recurred
                    # inside that one game.
                    "confidence": round(min(0.6, 0.2 + 0.05 * count), 2),
                    "learned_in": game_id,
                },
            }
        )
    return out


class MemoryStore:
    """Reads and writes ``mem:`` through a running Quipu.

    Thin on purpose. The orchestrator never learns the graph — it learns that memory was
    consulted — which is the same seam ``knowledge.py`` holds for retrieval.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        self.base_url = (
            os.environ.get("NA_MEMORY_QUIPU_URL", "") if base_url is None else base_url
        ).rstrip("/")
        self.timeout = timeout
        #: Reuses the datalinks client for its opener and its `query`, not for its retrieval
        #: semantics. Memory is a different plane and asks different questions.
        self._quipu = QuipuRetriever(self.base_url, timeout=timeout) if self.base_url else None

    @property
    def available(self) -> bool:
        return self._quipu is not None

    def write(self, extraction: Extraction) -> dict[str, int]:
        """Post a game's memories. Returns what was sent, per group.

        Two episodes, not one, because they belong to different time axes and different
        lifetimes: the per-game group is archived when the game ends, the durable one outlives
        it. Sending them together would need the reader to re-split by node type.
        """
        if self._quipu is None or not extraction:
            return {"game": 0, "durable": 0}

        sent = {"game": 0, "durable": 0}
        if extraction.nodes:
            self._post(
                {
                    "name": f"neural-amplifier game {extraction.game_id}",
                    "group_id": game_group(extraction.game_id),
                    "source": "neural-amplifier",
                    "nodes": extraction.nodes,
                    "edges": extraction.edges,
                }
            )
            sent["game"] = len(extraction.nodes)
        if extraction.tactics:
            self._post(
                {
                    "name": f"neural-amplifier tactics from {extraction.game_id}",
                    "group_id": DURABLE_GROUP,
                    "source": "neural-amplifier",
                    "nodes": extraction.tactics,
                    "edges": [],
                }
            )
            sent["durable"] = len(extraction.tactics)
        return sent

    def _post(self, payload: dict[str, Any]) -> None:
        import urllib.request

        request = urllib.request.Request(
            f"{self.base_url}/episode",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        assert self._quipu is not None
        with self._quipu._opener.open(request, timeout=self.timeout) as response:
            response.read()

    def recall(self, limit: int = 10) -> list[str]:
        """Durable tactics, highest confidence first, as prompt lines.

        Returns strings rather than structures for the same reason grounding does: the brain is
        told what was learned, not handed a graph to traverse.

        Any failure yields nothing. A store that is down costs an unaugmented prompt, and a turn
        that stalls waiting on last game's lessons has the priority exactly backwards.
        """
        if self._quipu is None:
            return []
        # `description` lands as rdfs:comment and the properties as aegis:<key>, which is
        # quipu's own mapping — established by writing an episode and reading the triples back.
        query = f"""PREFIX aegis: <{MEM_IRI}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?desc ?confidence ?observations WHERE {{
  ?t a aegis:Tactic ;
     rdfs:comment ?desc ;
     aegis:confidence ?confidence ;
     aegis:observations ?observations .
}}"""
        try:
            rows = self._quipu.query(query)
        except Exception:
            return []
        return _lines(rows, limit)


#: The full IRI the `mem:` prefix expands to when episodes are written. Quipu mints node IRIs
#: from the episode's names, so this is what a recall query has to name.


def _lines(rows: list[dict[str, Any]], limit: int) -> list[str]:
    """Rows as prompt lines, most-believed first."""

    def confidence(row: dict[str, Any]) -> float:
        raw = str(row.get("confidence", "0")).strip('"')
        try:
            return float(raw)
        except ValueError:
            return 0.0

    out: list[str] = []
    for row in sorted(rows, key=confidence, reverse=True)[:limit]:
        desc = str(row.get("desc", "")).strip('"')
        if not desc:
            continue
        obs = str(row.get("observations", "")).strip('"')
        conf = confidence(row)
        # The count and the confidence ride along because a tactic without them is an
        # instruction, and this plane is explicitly not entitled to give instructions.
        out.append(f"{desc} [learned: {obs} times, confidence {conf:.2f}]")
    return out


class RememberingRetriever:
    """Wraps a retriever and adds what earlier games taught — K3's recall half.

    A composition rather than a change to ``QuipuRetriever``, because the two answer different
    questions from different planes: retrieval says what the RULEBOOK says about the options in
    front of the brain, memory says what WE did last time. Folding them into one class would
    make "was this fact a rule or a habit" unanswerable at the point it matters, and a habit
    presented as a rule is the same failure the ``tier`` tag exists to prevent on the datalinks
    plane.

    Recall happens ONCE and is cached. Durable tactics are written between games, not during
    one, so asking per decision would be a network round trip on every turn for an answer that
    cannot have changed. The cache is the process, which is the same lifetime as the game.

    Works with no inner retriever at all: memory is worth having in an ungrounded run, and
    arguably worth more, because the brain has less else to go on.
    """

    def __init__(self, inner: Any | None, store: MemoryStore | None = None, limit: int = 10):
        self.inner = inner
        self.store = store if store is not None else MemoryStore()
        self.limit = limit
        self._recalled: tuple[str, ...] | None = None

    def _tactics(self) -> tuple[str, ...]:
        if self._recalled is None:
            self._recalled = tuple(self.store.recall(self.limit)) if self.store.available else ()
        return self._recalled

    def retrieve(self, world_view: WorldView) -> Grounding:
        base = (
            self.inner.retrieve(world_view)
            if self.inner is not None
            else Grounding(reason="no retriever configured")
        )
        learned = self._tactics()
        if not learned:
            return base
        # Appended, never prepended. The rulebook facts are about the options actually on the
        # table this turn; a tactic is a standing habit, and putting it first would give the
        # weaker claim the stronger position.
        #
        # `fact_ids` is deliberately NOT extended. Those are citable datalinks ids, and a tactic
        # has no node in that graph — minting a fake id would make CitationGuard resolve it to
        # nothing and report a fabricated citation on a fact we ourselves invented.
        return replace(base, facts=(*base.facts, *learned))
