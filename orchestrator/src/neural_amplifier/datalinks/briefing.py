"""The datalinks retriever — K1's exit criterion.

Two shapes, matching ``docs/quipu-integration.md``:

- **The static briefing**, paid once and prompt-cached for a whole game. It is
  the engine-filtered rules digest: what exists, what it costs, what unlocks it.
- **Per-turn grounding**, bounded to *this turn's* ``action_space``. This is the
  budget discipline that keeps the prompt from growing with the rulebook — a
  turn offering seventeen build options gets seventeen facts, not one hundred
  and thirty-three.

This implementation reads a parsed ``alphax.txt`` directly rather than querying
Quipu. That is K1 on purpose: it proves the seam and the briefing content with
no server, no embeddings, and no tokens, and K2 swaps the lookup for
``quipu_context`` without the orchestrator noticing — which is the whole point
of the ``Retriever`` protocol.

**No model is involved.** Composing a digest from structured rows is templating;
spending a model on it would buy paraphrase and risk invention, on exactly the
facts the datalinks plane exists to keep trustworthy.
"""

from __future__ import annotations

from ..contract import WorldView
from ..knowledge import Grounding
from .parse import Datalinks, Facility
from .rdf import slug

#: Only rules true for the engine in play may surface. A GLSMAC-only or
#: Thinker-deviating rule presented as canonical SMAC is the exact failure the
#: anti-masquerade guardrail exists to prevent.
CANONICAL_ENGINES = frozenset({"smac"})


def describe(item: Facility, links: Datalinks, compact: bool = False) -> str:
    """One line a model can act on: cost, upkeep, effect, and the gate.

    ``compact`` drops the two parts that are redundant *for every option equally* when the fact
    accompanies an action space. That evenness is the whole point: cutting retrieval by dropping
    whole facts under-explains the options it drops, so what is left has to shed the same kinds
    of content everywhere.

    That rationale used to be credited to na-373 as a measured result. It is not one — na-373
    was withdrawn, its numbers being one fact's rank on a single near-unanimous world view
    (``evals/runs/na-htm/NOTES.md``). The evenness argument stands on its own and does not need
    the citation; what *is* measured here is na-vbe, which found compacting this way left the
    choice intact, 20/20 against 19/20.

    **Cost goes** because the action space already carries an authoritative one: the adapter
    normalises the rulebook's "rows" into minerals with the faction's own cost factor, which
    varies by difficulty. This is the raw rulebook value. Emitting both puts "cost 4" from
    grounding beside "cost 44" from the action space *about the same thing* — the two-units
    error that already produced bad arithmetic once, which is why ``quipu.format_row`` has
    excluded cost all along. This function did not, so the two retrievers disagreed.

    **The prerequisite goes** because the option is *in the action space*: the engine has
    already ruled it buildable, so its tech gate is satisfied by construction. "needs Gene
    Splicing" about something you can build this turn is a fact with no decision attached.

    The briefing keeps both — it is a catalogue of what exists, read once, with no action space
    beside it to make either redundant.
    """
    parts = [item.name if compact else f"{item.name} — cost {item.cost}"]
    if item.maintenance:
        parts.append(f"upkeep {item.maintenance}/turn")
    if item.effect:
        parts.append(item.effect)
    if item.requires and not compact:
        techs = links.by_abbrev()
        named = [techs[a].name if a in techs else a for a in item.requires]
        parts.append(f"needs {', '.join(named)}")
    if item.secret_project:
        # NOT "one per game, globally", which is what this said and is not a rule the engine
        # has. `SecretProjects[]` records which base *holds* each project: several bases may
        # build the same one at once and the first to finish takes it, the rest lose the
        # minerals. A project whose base is destroyed goes `SP_Destroyed` — and returns to the
        # pool under Thinker's `rebuild_secret_projects`, which is a house rule and so cannot
        # be stated at canonical tier at all.
        #
        # Worth the comment because the old wording was ours, not alphax.txt's, and it shipped
        # on all 64 project lines of a briefing labelled canonical — the exact masquerade this
        # plane exists to prevent, and from the one source that is supposed to be above it.
        parts.append("secret project — only one base can hold it; others building it lose the race")
    return "; ".join(parts)


def briefing(links: Datalinks, engine: str = "thinker") -> str:
    """The cached-once static digest.

    Deliberately counts rather than enumerates the long tails: a model does not
    need all 88 technology rows in front of it every turn, and a briefing that
    grows with the rulebook is the thing budget discipline is protecting against.
    """
    available = [f for f in links.facilities.values() if not f.disabled]
    projects = [f for f in available if f.secret_project]
    ordinary = [f for f in available if not f.secret_project]
    techs = [t for t in links.technologies.values() if not t.disabled]

    lines = [
        f"SMAC datalinks (canonical rules; engine in play: {engine}).",
        f"{len(techs)} technologies, {len(ordinary)} base facilities, "
        f"{len(projects)} secret projects available.",
        "",
        "Base facilities (cost; effect; prerequisite):",
    ]
    lines += [f"  - {describe(f, links)}" for f in sorted(ordinary, key=lambda f: f.name)]
    lines += ["", "Secret projects (only one base can hold each):"]
    lines += [f"  - {describe(f, links)}" for f in sorted(projects, key=lambda f: f.name)]
    return "\n".join(lines)


class DatalinksRetriever:
    """Serves rules grounding for the actions actually on offer this turn.

    Satisfies :class:`~neural_amplifier.knowledge.Retriever`, so the orchestrator
    treats it exactly as it will treat Quipu.
    """

    def __init__(self, links: Datalinks, engine: str = "thinker", limit: int = 12) -> None:
        self.links = links
        self.engine = engine
        #: Ceiling on facts per turn. Rules before tactics under budget, and a
        #: bounded prompt before either (``quipu-integration.md`` §budget).
        self.limit = limit
        self._index = {f.name.lower(): f for f in links.facilities.values()}

    def retrieve(self, world_view: WorldView) -> Grounding:
        facts: list[str] = []
        ids: list[str] = []
        seen: set[str] = set()
        for action in world_view.action_space:
            item = self._match(action.action)
            if item is None or item.name in seen:
                continue
            seen.add(item.name)
            # Compact: this fact travels *with* an action space, which makes the cost and
            # the prerequisite redundant for every option alike (see `describe`).
            facts.append(describe(item, self.links, compact=True))
            # `fac:<slug>` — the same id `rdf.py` mints for this facility, so a citation made
            # against this retriever resolves in the graph the Quipu path serves. Without ids
            # the model has nothing to cite, `cited` is empty on every run by construction,
            # and utilisation reads unmeasurable rather than low.
            ids.append(f"fac:{slug(item.name)}")
            if len(facts) >= self.limit:
                break
        return Grounding(facts=tuple(facts), fact_ids=tuple(ids))

    def _match(self, action: str) -> Facility | None:
        """Resolve an action to a rule.

        Exact match only. A fuzzy match here would ground a decision in the
        wrong rule and label it canonical — silence is the safer failure.
        """
        return self._index.get(action.strip().lower())
