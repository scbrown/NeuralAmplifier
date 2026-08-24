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
from typing import TYPE_CHECKING, Any

from .contract import WorldView

if TYPE_CHECKING:
    # Imported for typing only: `intents` imports the predicate grammar from `queued`, and
    # keeping this out of runtime means the memory store stays importable on its own.
    from .intents import UnitIntent
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

#: The property every memory node carries to say whose it is, and the ONLY thing a read can
#: filter on.
#:
#: The fog design specifies named graphs — "a GRAPH clause in every SPARQL it issues". Measured
#: against the store this orchestrator actually reads (na-7bk): `GRAPH` is a hard error there,
#: and `FROM` is SILENTLY IGNORED — `FROM <a-graph-that-cannot-exist>` still returns rows. A
#: boundary built on `FROM` would look exactly like scoping and enforce nothing, which is the
#: failure the design's own mandatory leak-probe exists to catch. Stores DIFFER: another
#: deployment of this same store does not ignore `FROM`. Probe the store you are about to
#: read; never generalise from another one.
#:
#: So the scope is a TRIPLE, not a graph name. It is the same string either way, so a store that
#: grows real named-graph support later can adopt it without a migration.
SCOPE_KEY = "memoryScope"


def memory_scope(game_id: str, faction_id: int | str) -> str:
    """The graph one faction's memories live in.

    Per-FACTION, not merely per-game. An agent deciding for faction F must not decide with
    knowledge of another faction's private state, and a per-game group puts all six in one
    bucket — the boundary has to be drawn where the ruling draws it.
    """
    return f"{game_group(game_id)}:faction:{faction_id}"


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

    @staticmethod
    def _scoped(nodes: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
        """Stamp every node with the scope it belongs to.

        On the node, not on the episode. `group_id` is an episode-level label the write side
        applies and no read can filter on — which is why the read path was global despite the
        groups looking like a partition. A property survives into the triples, so a query can
        name it.
        """
        return [{**node, SCOPE_KEY: scope} for node in nodes]

    def write(self, extraction: Extraction, faction_id: int | str | None = None) -> dict[str, int]:
        """Post a game's memories. Returns what was sent, per group.

        Two episodes, not one, because they belong to different time axes and different
        lifetimes: the per-game group is archived when the game ends, the durable one outlives
        it. Sending them together would need the reader to re-split by node type.
        """
        if self._quipu is None or not extraction:
            return {"game": 0, "durable": 0}

        sent = {"game": 0, "durable": 0}
        # Unattributed memories are not written to a faction graph, because there is no honest
        # graph to write them to: a node whose faction is unknown cannot be scoped, and scoping
        # it to a guess is how one faction's private observation reaches another's prompt.
        scope = (
            memory_scope(extraction.game_id, faction_id)
            if faction_id is not None
            else game_group(extraction.game_id)
        )
        if extraction.nodes:
            self._post(
                {
                    "name": f"neural-amplifier game {extraction.game_id}",
                    "group_id": scope,
                    "source": "neural-amplifier",
                    "nodes": self._scoped(extraction.nodes, scope),
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
                    # Durable tactics are cross-game, cross-faction BY DESIGN: game-agnostic
                    # claims about SMAC mechanics, which any faction could have read in the
                    # manual. They are scoped to the shared group so a decision-loop read can
                    # union them in explicitly, rather than reaching them by having no filter.
                    "nodes": self._scoped(extraction.tactics, DURABLE_GROUP),
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

    # -- unit intent (na-7bk slice 3) ----------------------------------------
    #
    # The engine already persists a multi-turn goto: set_move_to keeps it, and door 2's `move`
    # issues one. What the engine does NOT keep is WHY — so a later decision sees a unit walking
    # east and has no way to tell a considered ferry run from a stale order nobody cancelled.
    # The intent node is that missing half: the engine executes, the graph remembers the reason,
    # and the agent re-decides only when something it named in advance changes.
    #
    # Faction-scoped like every other memory, and for a stronger reason than the rest: a unit
    # intent is faction-private BY DEFINITION (na-7bk fog ruling). Where a durable tactic can be
    # shared because any faction could read the manual, "I am massing rovers at the isthmus by
    # turn 60" is precisely the thing an opponent must not be able to recall.

    def write_intent(self, intent: UnitIntent, scope: str) -> None:
        """Record why a unit was given a long-horizon order.

        `scope` is required for the same reason `recall`'s is: an intent written without one has
        no faction, and a memory with no owner is one any faction can recall.

        Written at ORDER time rather than decision time, which is what makes the cost acceptable
        — the agent is acting at its own pace through door 2, not holding the engine's thread.
        """
        if self._quipu is None or not scope or not scope.strip():
            return
        self._post(
            {
                "name": f"neural-amplifier intent {intent.unit_id}",
                "group_id": scope,
                "source": "neural-amplifier",
                "nodes": [
                    {
                        "name": f"{MEM}intent/{scope}/{intent.unit_id}",
                        "type": "Intent",
                        "description": intent.line(),
                        SCOPE_KEY: scope,
                        "properties": {
                            "unit_id": intent.unit_id,
                            "goal": intent.goal,
                            "rationale": intent.rationale or "",
                            "until_turn": intent.until_turn if intent.until_turn else 0,
                            "triggers": "; ".join(t.describe() for t in intent.triggers),
                        },
                    }
                ],
                "edges": [],
            }
        )

    def recall_intents(self, scope: str, turn: int | None = None, limit: int = 10) -> list[str]:
        """This faction's live unit intents, as prompt lines.

        Scoped and fail-closed exactly as `recall` is — the same rule, because it is the same
        boundary and a second, laxer copy of it would be the hole.

        Horizon-filtered here rather than at write time: an intent is not wrong when it expires,
        it is finished, and a graph that deleted it would lose the record of what was intended.
        The prompt is what must not carry a plan whose horizon has passed, since that reads as
        though it still applied.
        """
        if self._quipu is None or not scope or not scope.strip():
            return []
        query = f"""PREFIX aegis: <{MEM_IRI}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?desc ?until WHERE {{
  VALUES ?scope {{ "{scope}" }}
  ?i aegis:{SCOPE_KEY} ?scope ;
     a aegis:Intent ;
     rdfs:comment ?desc ;
     aegis:until_turn ?until .
}}"""
        try:
            rows = self._quipu.query(query)
        except Exception:
            return []
        lines: list[str] = []
        for row in rows:
            desc = str(row.get("desc", "")).strip('"')
            if not desc:
                continue
            if turn is not None:
                try:
                    until = int(float(str(row.get("until", "0")).strip('"')))
                except ValueError:
                    until = 0
                if until and turn > until:
                    continue
            lines.append(desc)
        return lines[:limit]

    def recall(self, scope: str, limit: int = 10) -> list[str]:
        """What this faction has learned, highest confidence first, as prompt lines.

        `scope` is REQUIRED and there is no default. That is the fog boundary, and a default
        would be the hole in it: every caller that forgot the argument would read every
        faction's memories and look exactly like a caller that meant to. The decision loop must
        not be able to make a global read by omission — only by naming
        `recall_across_factions`, which is a different method with a different name for a
        different job.

        Fail-CLOSED. An empty scope recalls nothing and does not issue a query. The alternative —
        treating "no scope" as "everything" — is the same hole wearing a runtime cost.

        The deciding faction's own graph UNION `memory:durable`. Both, deliberately: durable
        tactics are game-agnostic claims about SMAC mechanics, which any faction could have read
        in the manual, so sharing them is not a leak. They are reached by being NAMED here, not
        by the query having no filter — the distinction that makes this testable.

        Returns strings rather than structures for the same reason grounding does: the brain is
        told what was learned, not handed a graph to traverse. Any failure yields nothing: a
        store that is down costs an unaugmented prompt, and a turn that stalls waiting on last
        game's lessons has the priority exactly backwards.
        """
        if self._quipu is None or not scope or not scope.strip():
            return []
        return self._recall(limit, scopes=(scope, DURABLE_GROUP))

    def recall_across_factions(self, limit: int = 10) -> list[str]:
        """Every faction's memories, unscoped. **Never in the decision loop.**

        Exists because analysis genuinely needs it — a post-game review reads all six factions,
        and that is not a leak because no decision is being made. Named this way so it cannot be
        reached by forgetting an argument, and so a call site that does not belong in the
        decision path is visible as one word in a diff.
        """
        if self._quipu is None:
            return []
        return self._recall(limit, scopes=None)

    def _recall(self, limit: int, scopes: tuple[str, ...] | None) -> list[str]:
        assert self._quipu is not None
        # `description` lands as rdfs:comment and the properties as aegis:<key>, which is
        # quipu's own mapping — established by writing an episode and reading the triples back.
        #
        # The scope filter is a plain triple pattern plus VALUES, NOT a GRAPH clause and NOT a
        # FROM. Measured on the store this reads (na-7bk): GRAPH is a hard error and FROM is
        # silently ignored there, so the two forms the design named would either refuse or —
        # worse — return unfiltered rows while looking scoped.
        scope_block = ""
        if scopes is not None:
            values = " ".join(f'"{s}"' for s in scopes)
            scope_block = f"  VALUES ?scope {{ {values} }}\n  ?t aegis:{SCOPE_KEY} ?scope .\n"
        query = f"""PREFIX aegis: <{MEM_IRI}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?desc ?confidence ?observations WHERE {{
{scope_block}  ?t a aegis:Tactic ;
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

    Recall is cached PER FACTION, and the per-faction part is the fog boundary at this layer.
    Durable tactics are written between games, not during one, so asking per decision would be a
    network round trip every turn for an answer that cannot have changed — but a single cache
    shared across factions is the leak in miniature: recall once while deciding for faction 2,
    then hand those lines to faction 4 without a query being issued at all. Scoping the store
    and sharing the cache would enforce nothing.

    The scope is derived per decision from the world view being answered, which is what "the
    orchestrator constructs the retriever per decision with the deciding faction's scope" means
    in practice — one object, one scope per call, never a scope carried over from the last one.

    Works with no inner retriever at all: memory is worth having in an ungrounded run, and
    arguably worth more, because the brain has less else to go on.
    """

    def __init__(
        self,
        inner: Any | None,
        store: MemoryStore | None = None,
        limit: int = 10,
        game_id: str | None = None,
    ):
        self.inner = inner
        self.store = store if store is not None else MemoryStore()
        self.limit = limit
        self.game_id = game_id
        self._recalled: dict[str, tuple[str, ...]] = {}

    def bind_game(self, game_id: str) -> None:
        """Tell the retriever which game it is serving.

        Separate from construction because the retriever is built before the Orchestrator that
        mints the game id. Until it is bound there is no scope to read under, and an unbound
        retriever recalls NOTHING rather than everything — the same fail-closed rule as
        `MemoryStore.recall`, at the layer above it.
        """
        self.game_id = game_id

    def prime_turn(self, turn: int, faction_id: int | None) -> int:
        """Pass the turn-boundary rulebook fetch through the memory wrapper."""
        primer = getattr(self.inner, "prime_turn", None)
        return int(primer(turn, faction_id)) if callable(primer) else 0

    def consultation_for(self, turn: int, faction_id: int | None) -> Any:
        """Pass the turn-boundary grounding *evidence* through too.

        Without this the wrapper silently swallows it: the orchestrator asks the retriever it
        holds, which is this object, and ``getattr`` finds no such method — so wrapping a
        Quipu retriever in memory would turn every grounded decision into one Yupana reports
        as ``missing``. That is the ``_with_latency`` failure again (``knowledge.py``): a
        pass-through that enumerates what it forwards drops whatever it was not told about.
        """
        inner = getattr(self.inner, "consultation_for", None)
        return inner(turn, faction_id) if callable(inner) else None

    def _scope_for(self, world_view: WorldView) -> str | None:
        """The deciding faction's graph, or None when we cannot say whose decision this is.

        None means no recall. A decision whose faction we cannot identify is exactly the one
        where guessing puts another faction's private memories into the prompt.
        """
        if not self.game_id:
            return None
        faction_id = getattr(world_view, "faction_id", None)
        if faction_id is None:
            return None
        return memory_scope(self.game_id, faction_id)

    def _tactics(self, scope: str | None) -> tuple[str, ...]:
        if scope is None:
            return ()
        if scope not in self._recalled:
            self._recalled[scope] = (
                tuple(self.store.recall(scope, self.limit)) if self.store.available else ()
            )
        return self._recalled[scope]

    def _intents(self, scope: str | None, turn: int | None) -> tuple[str, ...]:
        """Live unit intents for this faction (na-7bk slice 3).

        NOT cached, unlike tactics, and the difference is not an oversight. Durable tactics are
        written between games, so a cache costs nothing and saves a round trip per decision. An
        intent is written DURING the game, by this same agent, minutes ago — caching it would
        mean a plan the agent formed on turn 40 is invisible to it on turn 41, which is precisely
        the case the mechanism exists for.

        Horizon-filtered by the store against this turn, so an expired plan does not sit in the
        prompt reading as though it still applied.
        """
        if scope is None or not self.store.available:
            return ()
        return tuple(self.store.recall_intents(scope, turn=turn, limit=self.limit))

    def retrieve(self, world_view: WorldView) -> Grounding:
        base = (
            self.inner.retrieve(world_view)
            if self.inner is not None
            else Grounding(reason="no retriever configured")
        )
        scope = self._scope_for(world_view)
        learned = (*self._intents(scope, world_view.turn), *self._tactics(scope))
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
