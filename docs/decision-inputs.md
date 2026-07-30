# Decision Inputs

What Claude needs to see, per decision, to decide well.

Companion to [game-surface.md](game-surface.md), which inventories *which* decisions exist and
assigns each a tier. This document answers the next question: for a decision on the LLM tier,
**what has to be in the world view before the answer can be any good?**

It exists because the failure it prevents is silent. A brain handed a bare list of legal options
will always return one of them, and the record will look identical whether the choice was
reasoned or guessed. You cannot tell a well-grounded decision from a coin flip by reading the
orders — only by auditing the inputs. So the inputs get a checklist.

This is a **living document**. Every surface we move to the LLM tier gets an entry, and entries
get refined as play shows what was missing. An entry is never "done"; it is current.

---

## 1. The checklist

Eight categories. Not every surface needs every one, but every surface should be *asked* about
every one — the point is to make an omission deliberate rather than accidental.

| # | Category | The question it answers | Who supplies it |
|---|---|---|---|
| 1 | **Subject** | What is deciding, and what state is it in? | Adapter |
| 2 | **Action space** | What may it legally do — *and what does each option cost and do?* | Adapter (engine-authoritative) |
| 3 | **Local context** | What is nearby that bears on this choice? | Adapter (fog-limited) |
| 4 | **Strategic context** | What is the faction trying to achieve, with what resources? | Adapter |
| 5 | **Temporal context** | What changed recently, and what is already in flight? | Adapter |
| 6 | **Grounding** | What do the rules actually say about these options? | Orchestrator (Quipu) |
| 7 | **Fairness** | Which handicaps are active right now? | Adapter (computed) |
| 8 | **Exclusions** | What must Claude *not* see? | Adapter (fog gate) |

### 1.1 The two that get skipped, and why they matter most

**Category 2 is not "the list of legal moves."** A list of names is nearly useless: `Formers` vs
`Scout Patrol` is not a decidable comparison without cost, and a brain that cannot compare cost
will systematically over-pick expensive things. Every action needs at least **cost** and a
one-line **effect**. `Action` in [contract.md](contract.md) is `extra="allow"`, so this is
additive — no schema change required.

**Category 8 is a correctness requirement, not a nicety.** The temptation is to send everything
the engine knows, because more context reads as better decisions. But the engine knows things
this faction has not learned, and a brain that sees them will act on them — producing a player
that is subtly, unfalsifiably cheating. `fog.py` gates the diplomacy feed for exactly this
reason. **When in doubt, omit and record the omission** rather than send and hope the prompt
discourages use.

### 1.2 Cost discipline

Context is not free, and neither is latency. A decision that fires once per game can afford a
large world view; one that fires ten times per turn cannot. So each entry below records a
**budget** alongside its inputs, and a surface that fires often must justify every section.

The rule of thumb: **spend context where the decision is consequential and rare.** Tech choice
deserves a rich prompt. Per-base production, at ten bases a turn, does not.

---

## 2. `base.production` — what to build in a base

**Status:** first LLM-tier surface (A1). Fires per base per turn — *several times* per base per
turn at the engine level, gated to one decision by `call_seq == 1`
([headless-harness.md](headless-harness.md), and the `call_seq` rationale in
`thinker/src/neural.cpp`).

**Budget:** high frequency, low individual stakes. Keep it tight — this is the one surface where
terseness is a design goal.

| # | Category | Fields |
|---|---|---|
| 1 | Subject | base name, id, coords; size (population); nutrient / mineral / energy surplus; minerals accumulated toward the current item; current item and turns remaining; drone and talent counts; facilities already built |
| 2 | Action space | every legal build, each with: `id`, display name, **mineral cost**, **turns at current surplus**, category (unit / facility / project), one-line effect |
| 3 | Local context | garrison strength in this base; visible hostile units within a few tiles; distance to the nearest known hostile base; whether this base is coastal |
| 4 | Strategic context | faction energy reserve; base count; tech currently researched; social-engineering settings; who we are at war with |
| 5 | Temporal context | what this base built in the last few turns (avoids oscillation); whether the current item was chosen by Claude or by Thinker |
| 6 | Grounding | from `alphax.txt` via Quipu: what each offered facility/unit actually does, at **canonical** tier — never a mod's stats presented as canonical |
| 7 | Fairness | the active handicap ledger for this faction |
| 8 | Exclusions | anything outside this faction's fog; other factions' production; unmet factions' existence |

**Why category 5 is on this list.** Production decisions are re-evaluated constantly, and a
stateless brain will happily flip between two options every turn, accumulating nothing. Recent
history is what makes a *stable* choice possible.

**Known gap:** "turns at current surplus" requires mineral surplus, which the adapter has, but a
partially-built item changes the arithmetic. Ship cost and accumulated minerals and let the brain
do the division; do not pre-compute a number that will be subtly wrong.

---

## 3. `tech.choose` — which technology to research

**Status:** not yet implemented. Recommended as the **second** LLM-tier surface, and the first
where Claude should be expected to *outperform* the deterministic tier rather than merely match
it.

**Budget:** fires once per tech completion — every five to ten turns. Affordably rich. This is
where to spend context.

| # | Category | Fields |
|---|---|---|
| 1 | Subject | the faction: current techs known, research output per turn, accumulated research |
| 2 | Action space | each researchable tech: `id`, name, **cost in research points**, turns at current output, what it unlocks (units, facilities, projects, SE options) |
| 3 | Local context | n/a — this is a faction-level decision |
| 4 | Strategic context | faction agenda and character; SE settings and what we would *like* to adopt; military position; economic position; known rivals' apparent tech level |
| 5 | Temporal context | the last several techs chosen — a path, not a point. This is the whole reason the surface suits an LLM |
| 6 | Grounding | the tech tree from `alphax.txt`: prerequisites, and the full unlock set two steps out |
| 7 | Fairness | tech-cost handicaps, which differ by difficulty and by slot |
| 8 | Exclusions | rivals' exact tech holdings unless legitimately known |

**Why this is the surface that justifies the project.** Thinker picks techs with weighted tables.
It cannot reason "we are Gaians, we are boxed in on a small continent, and a naval-plus-ecology
path suits both our character and our terrain." That is a *path* argument over many turns, and it
is exactly what a language model is good at and a weight table is not.

---

## 4. `social.engineering` — choosing SE values

**Status:** not yet implemented. Strong LLM fit; low frequency.

| # | Category | Fields |
|---|---|---|
| 1 | Subject | current SE settings and the resulting effect totals |
| 2 | Action space | each legal SE combination, with its **net effect deltas** and any unlock requirement |
| 3 | Local context | drone/talent balance across bases — the constraint that usually binds |
| 4 | Strategic context | faction character and agenda; war or peace; growth vs military priority |
| 5 | Temporal context | previous SE choice and when it changed; pending revolt risk |
| 6 | Grounding | the SE table from `alphax.txt`, including faction-specific bonuses and prohibitions |
| 7 | Fairness | SE-related handicaps |
| 8 | Exclusions | other factions' SE settings unless known through contact |

**Note the faction-prohibition trap.** Factions are *forbidden* certain SE values. That is a rule
in `alphax.txt`, so it belongs in grounding — but it must **also** be enforced in the action
space, because grounding advises and the action space binds. An option Claude must not pick
should not be offered.

---

## 5. `unit.move` — where to move a unit

**Status:** deliberately **deterministic tier**, and this entry exists to record *why* rather
than to specify inputs.

Volume makes it unsuitable: dozens of units, every turn, each needing local terrain and threat
context. The token cost is enormous, the per-decision stakes are tiny, and Thinker's movement AI
is genuinely strong. It is also the surface where latency would be felt most — a turn cannot wait
on dozens of sequential model calls.

**If it is ever revisited,** the unit of decision should be an *operation* ("take that base",
"screen this approach") that the deterministic tier then executes, not individual tile moves.
That keeps the LLM at the altitude where it has an advantage.

---

## 6. Adding an entry

When a surface moves to the LLM tier:

1. Walk all eight categories. Write "n/a" with a reason rather than leaving a blank — a blank is
   indistinguishable from an oversight.
2. State the **budget** before the fields. Frequency drives everything else.
3. Name the **exclusions explicitly**. If the answer is "nothing", say so and say why.
4. Record **known gaps** at the bottom. A gap that is written down is a task; a gap that is not
   is a bug waiting to be misdiagnosed as a bad model.
5. After real play, come back and record **what was actually missing.** This is the step that
   makes the document worth keeping, and the one most likely to be skipped.
