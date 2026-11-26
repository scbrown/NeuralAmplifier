# Seats, policy and the console — many players in one game

How a human, a stateless model call and an attached agent harness all sit in the same game; how
an agent steers Thinker's deterministic AI for its own faction instead of answering every
decision; how what it learns is saved as scripts and reused in the next game; how nobody sees anybody else's hand; and how a person watches it happen.

Companions: [agent-play.md](agent-play.md) (the attached brain, as built),
[long-horizon-play.md](long-horizon-play.md) (the four cadences this fills in),
[tenancy-and-isolation.md](tenancy-and-isolation.md) (principals), [observability.md](observability.md)
(the dashboard this extends), [thinker-adapter-notes.md](thinker-adapter-notes.md) (slot modes).

**Status: DESIGN. Nothing in this document is built.** Where it leans on something that exists,
it says so and names the file. Line references into the Thinker fork are against `master`
`94f9caf` (2026-08-27) and were read, not run.

---

## 0. What was asked for

Rulings from Stiwi, 2026-11-26, recorded in Quipu under `NeuralAmplifier`:

1. Any harness — an `st` crew member, Claude Code — can play the game. A person can play **with**
   it and **against** it, and watch its behaviour in a web console while playing.
2. Interactive play happens on Linux, on `vati`.
3. Interactive play on the Claude Code subscription lane counts inside the spend envelope, and
   any budget is fine for it. It is still metered and reported (§8).
4. Opponents must not be able to see other players' ideas and actions.
5. Agents can modify Thinker's deterministic actions **for their own faction exclusively**: the
   LLM thinks strategically and executes tactics through that determinism.
6. The design uses what the Quipu stack already ships.
7. The work gets a dedicated `st` crew lane and a dedicated seeds lane.
8. Deterministic play takes its cue from Voyager ([arXiv 2305.16291](https://arxiv.org/abs/2305.16291),
   [MineDojo/Voyager](https://github.com/MineDojo/Voyager)): saved scripts, kept in Quipu, reused
   in later games.

## 1. Where the repo stands

Both ways of putting a model in the loop are built, behind one `Brain` protocol (`brain.py`):

| Brain | What it is | Cost of a decision |
| --- | --- | --- |
| `agent` (`agent_brain.py`) | parks the decision on a queue; an MCP client collects and answers | 30–60 s, game blocked |
| `claude-code` (`claude_code_brain.py`) | one fresh, isolated `claude -p` per decision | seconds, no memory between calls |
| `claude` (`brain.py`) | Anthropic SDK | median 3.0 s on Haiku over 723 decisions (`evals/runs/na-mmp`) |
| `scripted` | deterministic, for CI | none |

Three things stop that from being a game a person can sit down to:

- **The brain is chosen once per process** (`build_brain`, `service.py`) for every faction and
  every surface. A human, a `claude -p` opponent and a crew member cannot share a game.
- **The queue is one FIFO.** `DecisionQueue.claim` takes the oldest pending decision whoever
  asks; faction is a parameter the caller supplies on the reads that have one.
- **Every decision is a model call.** The last long run on `vati` made 1,817 LLM decisions in
  173 turns for one faction, about ten a turn, each one freezing the game window. Six opponents
  at that rate is not a turn-based game, it is a queue.

`long-horizon-play.md` §3 already names the cure for the third: four cadences, of which the
bottom two (turn, decision) are built and the top two (doctrine, campaign) are not, and its
build order says the strategic review is the next thing to build because fifty per-decision runs
issued zero directives. This document is where that lands.

## 2. Seats

A **seat** is one faction in one game, the principal that occupies it, and the drivers that act
for it.

```toml
# na.toml
[seats.3]                       # faction id
principal  = "malcolm"          # who the seat belongs to: a crew member, "stiwi", or a brain name
strategist = "agent"            # agent | human | none   — slow cadence, never blocks the engine
reflex     = "skills"           # skills | policy | claude-code | claude | agent | scripted
budget_usd = 10.00              # metered ceiling; see §8

[seats.7]
principal  = "stiwi"
strategist = "human"
reflex     = "copilot"          # propose, then wait for approve/override in the console
```

- **`SeatRouter` is a `Brain`.** It reads the faction off the world view — which the engine
  asserts, not the caller — and dispatches to that seat's reflex driver. `Orchestrator.decide`
  does not change; everything it guards stays around the one line that reaches a brain.
- **A faction with no seat is not routed.** `llm_factions` in `thinker.ini` is *derived* from the
  seat table by `play-thinker.sh` rather than written beside it, so the two cannot disagree.
- **Slot is reported, not configured.** Whether a seat is on a human or an AI slot comes from the
  adapter's `slot` field. It matters: see §4.4.

The two roles map onto the two brains that already exist, which is the answer to "attached
harness or `claude -p`":

| Role | Cadence | Best driver | Blocks the game |
| --- | --- | --- | --- |
| **Strategist** | doctrine, campaign, strategic review | attached harness — has memory, tools, time | never |
| **Reflex** | a raised decision | `skills` (§5) over `policy` (§4), else a stateless model call | only for a model call |
| **Fallback** | always | Thinker's deterministic tier | no |

A seat may still set `reflex = "agent"` to have a harness answer every decision. That is the
mode `agent-play.md` describes, it stays, and it is slow.

## 3. Isolation

The rule is ruling 4, and `tenancy-and-isolation.md` already states its strength: *at least as
strict as fog of war*. What follows is what enforces it and, honestly, what does not yet.

### 3.1 What leaks today

Read from source; none of this is a claim about a running game.

| Leak | Where |
| --- | --- |
| Faction identity is caller-asserted on the agent tools | `turn_forecast`, `submit_turn_plan`, `deferred_decisions` take `faction_id` as an argument |
| The queue hands any decision to any caller | `DecisionQueue.claim` |
| The observer lane is unscoped and unauthenticated | `GET /turn`, every `/dashboard/api/*` route |
| One decision log and one world-view store per run | `decisions.jsonl`, `worldviews/` — readable by any process on the host |
| The command channel is an unauthenticated file | `na-command`; `apply-tech` and `apply-se` take any faction id, `apply` and `apply-hurry` any base (fork `neural.cpp:6900–7102`) |
| `game-state` reports every faction's base count | fork `neural.cpp:7733` |
| Yupana tenancy is caller-asserted | `game_id` and `faction_id` are plain arguments, no auth in `src/state` |
| Quipu reads are open | `server.auth_token` gates writes only; scoped reads are default-off, HTTP-only and not deployment-accepted |

### 3.2 Three tiers of enforcement

**Tier 1 — the API. Build first; it is all orchestrator.**

- **Seat tokens.** At game start the orchestrator mints one opaque token per seat. The MCP server
  is started with `--seat-token`; every agent-facing route derives the faction from the token and
  the `faction_id` parameter is removed from the tool surface. A token for seat 3 cannot name
  seat 5, because there is nothing to name it with.
- **Scoped claim.** `claim` returns only decisions for the token's seat.
- **Per-seat evidence.** One decision log and world-view store per seat under
  `runs/<game>/seat-<n>/`, plus the operator's merged view. A seat can be handed its own record
  without being handed the game.
- **Console views** are tokens too (§7).

**Tier 2 — the process.** A crew member on `vati` shares a unix user and a filesystem with the
orchestrator, the run directory and the game's save files, so Tier 1 stops an honest agent and
not a curious one with a shell.

- A **seat harness profile**: the playing session has the Neural Amplifier MCP server and
  nothing that reads the run directory. `claude -p` already runs this way (`--tools ""`, empty
  temp directory). An attached crew seat needs the same thing stated as permission rules, and a
  leak-probe test that tries to read a sibling's log from inside one.
- **The command channel is the orchestrator's alone.** In the fork, every mutating verb goes
  through `na_order_gate` with the faction derived from the entity, and the `apply*` verbs and
  fixtures are refused unless an operator flag is set in `thinker.ini`.

**Tier 3 — the stores.**

- **Memory: one Quipu database per seat identity**, plus the shared read-only datalinks dataset
  that exists today. This is what `tenancy-and-isolation.md` already concludes, for the reason
  the stack map confirms: a group id is provenance, not isolation. Quipu's scoped-writer grants
  (shipped) pin a seat's writes to its own graph; they do not restrict reads, which is why the
  boundary is the database and not the graph.
- **Board: only the orchestrator calls Yupana**, with the tenant derived from the seat token. An
  agent never holds a Yupana URL.

### 3.3 The person is a player too

When Stiwi plays against an agent, the console is the leak. So the console has a **player view**
(your own seat in full; opponents show liveness, latency and counts only) and a **spectator
view** (everything). Opening the spectator view on a game you are playing writes
`observer_spoiled: true` to the run record, so the result stays interpretable. Opponents'
reasoning is released when the game ends, as a replay.

## 4. Policy — strategy from the model, tactics from Thinker

Ruling 5. `turn-scoped-play.md` describes two doors into the game. This is the third.

| | Door 1 — decision hook | Door 2 — command channel | **Door 3 — policy** |
| --- | --- | --- | --- |
| Who starts it | the engine asks | the agent orders one unit or base | **the agent sets a standing posture** |
| Model calls | one per decision | one per order | **one per review** |
| Who executes | the model's pick | the model's order | **Thinker's own AI, under the posture** |

### 4.1 What can be steered

There is **no per-faction configuration in Thinker today**. `Config` is global (fork
`main.h:136–290`) and every weight in the production, movement, social and tech routines is a
hard-coded constant. But every read site has a faction id in scope, so a per-faction overlay is
mechanical rather than architectural.

Three kinds of knob, cheapest first:

| Kind | What | Cost in the fork |
| --- | --- | --- |
| **Existing per-faction fields** | `Faction.AI_fight/growth/tech/wealth/power` (about 60 read sites across production, social engineering, tech and energy), `player_flags` strategy bits, `base_id_attack_target`, `Faction.goals[]`, per-base `governor_flags` | none — they are set once at spawn, never recomputed, and saved with the game |
| **Clamps on recomputed plans** | `AIPlans` fields, per-base `defend_goal` | two seams, at the end of `plans_upkeep` and `move_upkeep`; `AIPlans` is zeroed every turn, so anything written earlier is lost |
| **Constants made overridable** | pod cap, former cap, threat multiplier on combat builds, minimum combat odds, home-defence duty cycle, hurry reserve multiplier, project and satellite limits | eight to ten one-line seams, each with a row in `tests/na_seams.tsv` |

The first policy vocabulary, in order of leverage:

1. **Focus** — the five `AI_*` weights. One knob moves production, social engineering, tech and energy together.
2. **Expansion** — pod cap and expansion limit.
3. **Military share** — the threat multiplier that gates combat builds.
4. **Aggression** — minimum-odds offset and the home-defence duty cycle.
5. **Target** — attack target base and attack goals.
6. **Defence floor** — per base.
7. **Formers** — cap, fungus and tube policy.
8. **Hurry reserve** — how much energy is held back.
9. **Social priorities.**
10. **Project and satellite limits.**

**Not steerable, and the design should not pretend otherwise:** `enemy_strategy`,
`enemy_diplomacy`, the legacy `enemy_move`, and the engine's own `social_ai` and `base_hurry` are
raw addresses in `terranx.exe` with no Thinker override. Diplomacy stays a decision surface.

### 4.2 How a policy reaches the game

**In the reply to `/turn`, not on the command channel.** The adapter already posts the turn
announcement at `mod_turn_upkeep` and discards the reply (fork `neural.cpp:4555`). Returning the
seat's policy there has three properties the file channel lacks: the faction id is asserted by
the engine, it arrives before that turn's decisions are made, and it costs no extra round trip.
The adapter stores it in `NaPolicy na_policy[MaxPlayerNum]` and consults it only where
`llm_enabled(faction)`.

**Exclusivity is structural.** A policy is keyed by seat token on the way in and by the engine's
own faction id on the way out. There is no call that takes a policy and a faction as separate
arguments.

**The overlay is write-only.** `AIPlans` is omniscient: `plans_upkeep` sums every faction's
units. Echoing computed plan state back to a model would be a fog leak through the policy door.
A seat reads back what it *set*, never what Thinker derived.

### 4.3 A policy is a governed record

Same discipline as a directive, which refuses a metric nobody reports:

- **A frozen knob registry**, like `surfaces.py`: id, type, range, default. Mirrored in the
  fork and diffed mechanically, the way `test_adapter_contract.py` diffs emitters.
- **A change needs a reason, a horizon and a review trigger.** A posture nothing will revisit
  reads forever as a plan somebody is watching.
- **Every change is a fact in the seat's own Quipu store**: who set which knob, when, why, and
  what it superseded. Bitemporal reads answer "what posture was this faction under at turn 60".
  SHACL validates ranges on write. Quipu's fluents are the natural shape for a value over time,
  but their served schema is not activated yet, so this starts as plain triples.
- **Every decision record carries `policy_hash`.** A deterministic-tier decision made under an
  agent's posture is not the same evidence as one made under Thinker's defaults, and the record
  must say which.

### 4.4 The slot decides which knobs are live

On an **AI slot** Thinker's whole AI runs, so every knob above takes effect. That is the
configuration for playing *against* an agent, and it is where policy ships first.

On the **human slot** several levers are dead, because the routines return early on `is_human`:
`allow_expand` bypasses the expansion limit, `mod_social_ai` returns, `invasion_plan` returns so
no land target is ever chosen, and `design_units` returns. Lifting those gates for an
LLM-routed human slot is the same work `long-horizon-play.md` §7 already asks for, and
`unit-turn` has to be on by default before a policy has any units to steer.

**More than one agent seat needs three singletons made per-faction first:** `plan_upkeep_turn`,
`na_withdraw_target` and `na_unit_turn_mode` are process globals and would collide.

### 4.5 What it should buy, and how to tell

Expected, not measured: model calls per turn fall from about ten per faction to roughly none,
with a strategist review every ten turns or on a trigger. That is the difference between a round
that takes minutes and one that takes as long as stock Thinker does, and between a game that
costs tens of dollars and one that costs a few.

The instrument already exists. `fixture_arms.sh` runs one save under several setups from
byte-identical state. Exit for the first cut: two policies from the same save produce a
measurably different build mix over twenty turns, and the engine-emitted `na.outcome.v1` stream
shows the difference.

A seat on `reflex = "policy"` still routes the surfaces a posture cannot express — tech choice,
social engineering, council votes — to a model call. Those fire a few times a game, not a few
times a turn.

## 5. Skills — saved scripts that outlive the game

Ruling 8. A policy knob is a number; a **skill** is a program that sets knobs, answers
decisions and issues orders, is stored once, and is run again in the next game with no model in
the loop.

### 5.1 What Voyager did

Voyager is an LLM agent that plays Minecraft by writing code rather than choosing actions. Three
parts, in the authors' words: an automatic curriculum, "an ever-growing skill library of
executable code", and an iterative prompting mechanism that uses environment feedback, execution
errors and self-verification. Read from the repository (`voyager/agents/skill.py`) and the paper:

- **A skill is a JavaScript function** over a small set of hand-written control primitives.
  Skills call earlier skills, so behaviour compounds.
- **Adding one**: a model writes a one-line description from the code; the description — not the
  code — is embedded into a vector store under the function's name; the code is written to
  `skill/code/<name>.js`.
- **Retrieving**: embed the current task, take the five nearest descriptions, and put their code
  in the prompt.
- **Revising**: a rewrite of an existing name is saved as `<name>V2.js` and replaces the old
  entry in the index. The old file stays on disk but nothing points at it.
- **Admitting**: a critic model judges whether the task succeeded. A task that is not solved in
  four rounds of rewriting is dropped and the curriculum proposes another.
- **Reusing**: a new world is started with `skill_library_dir` pointing at an old library.

### 5.2 The same shape here

| Voyager | Neural Amplifier |
| --- | --- |
| Control primitives (`mineBlock`, `craftItem`, …) | the **seat API**: read your own world view, choose from the action space, set a policy knob, queue a standing answer, issue `move` / `skip` / `build`, call another skill |
| A skill: JavaScript run by Mineflayer | a skill: a deterministic script run by the orchestrator |
| Environment feedback | `order_outcomes` and the engine's `na.outcome.v1` events |
| Execution errors | script errors, plus every refusal: validation, the guard, the order gate |
| Self-verification by a critic model | a **success check** written in the metric vocabulary, judged by the engine's own numbers |
| Automatic curriculum | the strategist's review, and a practice ladder over save fixtures |
| Chroma plus a directory of files | a governed graph in Quipu |
| `skill_library_dir` | the seat's own book plus the shared commons, attached to every game it plays |

Two of those rows are improvements rather than translations.

**The critic is the game.** Voyager asks a model whether a task succeeded. A skill here declares
what success means before it runs — "at least four bases by turn T+25" — in metrics the world
view already reports, which is the same rule that makes a directive refusable. A skill with no
checkable outcome is refused at authoring time.

**Practice needs no opponent and no person.** `fixture_arms.sh` already runs one save under
several setups from byte-identical state. That is Voyager's rewrite loop with a fixed starting
point: write the skill, run it from the fixture, read the errors, refusals and the success check,
revise, at most four rounds, then admit or shelve. While a trial runs, a seat on `reflex =
"skills"` spends nothing; the model calls are the authoring ones.

### 5.3 What a skill is

```python
# expand_until.star — a sketch, not a committed grammar
def on_turn(view, want_bases, hold_reserve):
    if view.metrics["bases"] >= want_bases:
        return done()
    set_policy("expansion.pod_cap", 3)
    set_policy("hurry.reserve", hold_reserve)

def on_decision(view, want_bases, hold_reserve):
    if view.surface_id != "base.production":
        return None                      # not mine: fall through
    pods = [a for a in view.action_space if a.role == "colony"]
    if pods and view.metrics["mineral_surplus"] >= 2:
        return choose(pods[0].id, "expand while the base can pay for it")
    return None                          # let Thinker pick under the policy
```

- **Two entry points.** `on_turn` runs when the turn is announced and may set policy, queue
  answers and issue orders. `on_decision` runs on a raised decision and returns an action id or
  nothing. Nothing means *fall through* — to the policy, then to Thinker.
- **A skill is a `Brain`.** `SkillBrain` implements the same protocol as every other driver, so
  its answer goes through validation, the guard and the record exactly as a model's would. It
  cannot name an action the engine did not offer. Records land at `tier = "skill"` with the
  skill's name and content hash.
- **Deterministic and hermetic.** No clock, no network, no file, no randomness, a step budget.
  The language is Starlark (ruled 2026-11-26), which is built for exactly this and reads like
  the Python a model writes well; `starlark-pyo3` wraps the Rust implementation. The property
  that matters is not the language: the same world view must always produce the same answer, because that is
  what lets `neural-amplifier replay` re-run a recorded game against a changed skill with no
  game running and report an exact diff.
- **Parameters, not literals.** A skill that names base 7 or tile (13, 8) works in one game. A
  lint refuses literal entity ids and coordinates; a skill finds its subject through the view.
- **It reads one seat.** The primitives expose the seat's own world view and nothing else. The
  rule in `agent-play.md` — no second source of board state — covers scripts as it covers tools.

Answering from a skill costs what answering from a plan table costs, which was measured at a
median of 0.9 ms (`agent-play.md` §5).

### 5.4 The library in Quipu

One `na:Skill` entity per version, shape-checked on write:

| Property | Why it is there |
| --- | --- |
| name, description, embedding | retrieval: the nearest five by description, as Voyager does |
| code and its content hash | the hash is what a decision record cites |
| `supersedes` | a rewrite is a new version, and the old one stays queryable; "the library as it stood before game 12" is a bitemporal read rather than a lost file |
| `calls` | skills composing skills is a graph, so "what breaks if this one changes" is a query |
| `requires` | the knob and surface ids it touches; a skill needing a knob this engine build lacks is filtered out before it is offered |
| applicability | engine, ruleset tier, slot mode, game phase; retrieval filters on it before ranking |
| success check | metric, comparator, target, horizon |
| trials | each run: game or fixture, seed, verdict, the metric deltas |
| status | `candidate`, `verified`, `retired`; only `verified` skills answer in a live game |
| provenance | which seat identity wrote it, in which game and turn, with which model |

Retrieval is Quipu's hybrid search over descriptions, filtered by applicability and status, then
the strategist chooses, parameterises and composes. That is where the model's judgement goes:
*which* skills, for *this* board.

### 5.5 Private during the game, shared after it

Ruled 2026-11-26: a skill book is private while a game is in progress and shared once it ends.

- **During a game** a seat reads two things: its own book, in its own Quipu database (§3.2), and
  the **commons** — every skill shared so far, read-only. What a seat writes or revises
  mid-game goes to its own book and nowhere else, which is ruling 4 applied to strategy.
- **The commons is read as of the moment the game started.** Quipu reads are bitemporal, so a
  seat's retrieval is pinned to the game-start transaction. Nothing published while the game is
  running can reach an opponent in it, even if another game ends in the meantime.
- **When the game ends**, each seat's new and changed skills are published to the commons with
  their trials and provenance. Publication is a recorded step of ending a game, not a side
  effect of writing a skill, and an abandoned game publishes nothing until it is closed.
- **Status travels with the skill.** A `candidate` is published as a candidate. Sharing a skill
  does not verify it.

### 5.6 What does not carry over from Minecraft

- **An opponent moves.** A Minecraft skill fails for reasons inside the skill. A skill here can
  be sound and still lose to what another faction did. One trial is an anecdote; admission needs
  several seeds, and the verdict is a rate.
- **The clock is the game.** Voyager can retry for as long as it likes. A skill that reaches its
  target forty turns late has failed, which is why the horizon is part of the check.
- **The primitives are thinner.** Voyager's are rich because Mineflayer is. Ours are as wide as
  the applied surfaces, the order verbs and the knob registry, and the library can only be as
  expressive as §4 makes the deterministic tier steerable.

### 5.7 Where a script runs, and what it reaches in Thinker

**Starlark does not run inside Thinker, and should not.** The adapter is a 32-bit Windows DLL,
cross-compiled with `i686-w64-mingw32-g++`, loaded into `terranx.exe` under Wine, inside an
engine that is not thread-safe. An interpreter there would break "keep the adapter thin"
(invariant 3), tie every skill to one engine, and put a second place to crash inside the turn
loop. Skills run in the orchestrator, where `starlark-pyo3` is one Python dependency, and reach
the game only through the contract — which is already how every brain reaches it. Thinker never
learns that a script exists, and the GLSMAC adapter gets skills for nothing.

So interoperability is not a language question. It is a question of **which wire a primitive
rides**, and the three wires differ by three orders of magnitude:

| Primitive | Wire | Cost | Good for |
| --- | --- | --- | --- |
| `choose(action_id)` | the reply to `/decide`, a call the adapter already makes | about a millisecond on the orchestrator side (0.9 ms median at the plan tier); the full trip through Wine is not measured | every decision the engine raises: production, hurry, tech, social engineering, council |
| `set_policy(knob, value)` | the reply to `/turn` | once per faction per turn, answered from a stored value with no model call; on a timeout the adapter keeps the last policy | everything Thinker's own code decides: unit movement, terraforming, combat, expansion |
| `order(move / skip / build)` | the command channel | about 250 ms an order — it is a file polled at 4 Hz | a handful of deliberate moves a turn, never a unit loop |

The consequence is the division of labour in ruling 5, and it is forced rather than chosen: **a
script cannot micro-manage units.** The engine does not raise a decision per unit move — 25
unit-scope surfaces are uninstrumented — and fifty orders is twelve seconds. What moves an army
is Thinker's C++, and a script steers it through knobs. Starlark decides *what posture*; C++
decides *which tile*.

**When a knob is too coarse**, the answer is a richer table, not an embedded interpreter. A
skill can emit data the fork consults at a seam — a weight per build category, a priority per
base, a list of target regions — carried in the same `/turn` reply. Tables keep the adapter a
reader of numbers. If a case ever truly needs logic inside the engine's loop, Lua is the
language that builds trivially for that toolchain; that would be a deliberate exception to
invariant 3, and nothing in this design needs it.

## 6. The strategist is an attached harness

A crew member holding a seat is the planning occasion the build order has been missing.

- **It wakes for a review, not a decision**: every N turns, on a trigger from the metric
  vocabulary, or on a discontinuity (a base lost, a war declared). It reads its seat's trajectory
  and ledger, then selects and parameterises skills, issues directives, changes policy, or
  writes a new skill — all through routes that install without blocking (`/agent/directive`,
  `/agent/plan`, and the new policy and skill routes).
- **The game never waits for it.** A missed review leaves the standing policy in force and
  records `review_missed`. Invariant 9 holds again for policy seats.
- **The doorbell becomes a command template.** Today it is `tmux send-keys` to a pane. For a
  crew member it is `st inbox -d <agent>`, which persists before it types. The rule in
  `doorbell.py` does not change: a fixed sentence and an id, no game data.
- **Chaski is the eventual owner of that nudge.** A rule over the seat's graph ("review due")
  emitting to an inbox is exactly its shape, and shantytown ships the bridge. Its emitter stage
  and that bridge are not production-enabled and its event poll floor is 60 s — fine for a
  review, useless for a decision — so the template comes first and the rule replaces it.
- **Memory is the campaign ledger, in the seat's own store.** Camayoc's handoff and decision
  entities are the right pattern (superseding natural keys, shape-checked writes) but the wrong
  vocabulary: a handoff's next step points at a board item. The ledger gets game terms —
  commitment, review, policy change — proposed through Camayoc's ontology process.

## 7. The console

Built on the existing dashboard (`dashboard.py`, served on `vati` port 8088), which is a
read-only archive polled every five seconds. Rikuy is a static site whose live readers ship
disabled, so it is not the host for this.

- **A live stream.** Server-sent events, filtered server-side by the viewer's token: decision
  raised, claimed and by whom, answered, guard verdict, applied or diverged. Fed from the same
  `Emitter` that writes the log, as one more sink, so it cannot disagree with the record.
- **Now.** What is open this second, which seat is thinking, and for how long. This is the pane
  that makes waiting legible.
- **Seats.** Driver, standing directives, turn-plan hits and misses, latency, degrade rate,
  spend against budget.
- **Policy.** Each seat's knobs in force and the change log with reasons — the direct view of
  "what is it trying to do".
- **Copilot.** For a seat on `reflex = "copilot"`: the proposal, the reasoning, approve or
  override, and what happens on timeout.
- **Graph**, later: the seat's Quipu store through a server-side proxy of `/changes/stream`.
  Quipu's feeds are not authorised per graph, so a browser never talks to them directly.

The game itself is a Wine window on `vati`'s display; the console sits beside it or on a second
device at `vati.lan:8088`.

## 8. Spend

Ruling 3: any budget is fine for interactive play, and it is still counted. `claude -p` reports
a cost per call and the record already carries it.

- Each seat has `budget_usd`, a meter with a ceiling. A seat that reaches it keeps playing on
  its skills and its last policy, recorded as a budget stop rather than a degradation.
- The console shows the meter per seat and per game.
- The default follows the existing rule on `na-xb1`: up to USD 50 a game, proceed and report.

Skills and policy are the real control. A seat that answers every base spends on bases; a seat
that reviews every ten turns spends on reviews; a seat running verified skills spends nothing
between reviews.

## 9. The Quipu stack, need by need

| Need | Shipped | Gap to build |
| --- | --- | --- |
| Private per-seat memory | a Quipu database per seat; scoped-writer grants | provisioning per seat; reads are not scoped |
| Shared rules | the existing datalinks dataset | none to speak of |
| Live console feed | Quipu `/changes/stream`, `/feed/ws` with a graph filter | feeds are not per-graph authorised; needs a server-side proxy |
| Waking an attached agent | `st inbox -d` | Chaski emitters and the inbox bridge are pilot |
| Guard and what-if on the board | Yupana `game-state`: ingest, guard, what-if, per-faction overlays | the installed 0.19.4 binary lacks the feature; tenancy is caller-asserted |
| Strategist handoff | Camayoc's entity pattern | a game vocabulary; per-seat store routing |
| Governed policy | `aegis:Policy` rows projected to Yupana | policies are not faction-scoped |
| Skill library | hybrid search, SHACL on write, bitemporal reads pinned to game start, per-database isolation | the `na:Skill` vocabulary and shapes; a commons dataset; the publish step at game end |
| A game as an auditable run | Shuttle, through `export-run` | post-hoc only; no live signed steps |
| Refused tool calls per seat | Desire Path's failure hook | wiring |
| Work tracking | an epic on the existing board | `st` reads one seeds store per deployment, so a second project is not workable today |

## 10. Build order

Each step is playable or measurable on its own.

1. **Lane and host.** Stand up the crew and the tracker. Bring `vati` back to a running game: no
   decision has landed there since 2026-08-24, and the dashboard service is on the scripted
   brain.
2. **A first game, no new code.** A person on the human slot, one opponent routed through
   `llm_factions`, `NA_BRAIN=claude-code` on a fast model, the existing dashboard. Exit: ten
   turns played, `llm_decisions > 0` on `/coverage`, and the wait between turns written down.
   That number sizes everything after it.
3. **Seats and tokens** (orchestrator) **and the live console** (dashboard), in parallel — they
   touch different modules. Exit: three seats with three different drivers in one game, a leak
   probe that fails to read a sibling, and a Now pane that shows who is thinking.
4. **Skills over what exists.** `SkillBrain`, the hermetic runner, the lint, and the `na:Skill`
   shapes — with the primitives that need no fork change: choosing from an action space, queued
   answers, the order verbs. Needs no game: skills run against the captured world views in
   `orchestrator/tests/fixtures/captured`. Exit: one hand-written skill replayed over a recorded
   game with an exact diff, stored in Quipu, retrieved by description, and run again.
5. **Policy, first cut.** The existing per-faction fields, on an AI slot, carried in the `/turn`
   reply, and exposed to skills as `set_policy`. Exit: the `fixture_arms` result in §4.5, and a
   twenty-turn game in which the opponent seat makes a counted, small number of model calls.
6. **The practice loop.** Author, trial from a fixture, read the feedback, revise, admit. Exit:
   a skill a model wrote reaches `verified` over several seeds with no person involved, and a
   second game on a different map uses it.
7. **The strategist seat.** A crew member attached, reviews on cadence, the inbox doorbell, the
   ledger and the book in a per-seat store. Exit: a commitment opened at turn T, reviewed at
   T+10, and a skill selected because of it — the carry `long-horizon-play.md` asks for.
8. **Policy, second cut, and copilot.** Constants made overridable, the human-slot gates lifted,
   the three singletons made per-faction, and approve-or-override in the console for your own
   faction. Exit: two agent seats in one game; a person playing a faction under a posture they
   set.
9. **Isolation hardening.** The seat harness profile, the command-channel gate, per-seat Quipu
   stores, and leak probes for each.

Step 2 is what puts a game in front of a person soonest. Steps 4 to 6 are what make it fast,
cheap, and better in the next game than in this one.

## 11. Open questions

1. **Does a policy seat on an AI slot count for anything on the ladder?** `na-xb1` pins the
   measured faction to the human slot because AI slots carry handicaps. Playing *against* an
   agent puts it on an AI slot by construction. These runs carry their fairness profile and
   `ab-outcomes` already refuses a mismatch, so they cannot contaminate the ladder; whether a
   handicap-neutral difficulty exists for casual play is unanswered.
2. **How much of a turn is free thinking time?** A strategist can plan while a person takes
   their own turn only if the forecast lands early enough. Not measured.
3. **What does `enemy_strategy` do for a human slot?** Unverified, and it bounds how much of the
   target knob is real there.
4. **What a `/decide` round trip costs through Wine.** The orchestrator answers a skill in about
   a millisecond; the adapter's HTTP client and the Wine boundary are not in that number, and
   they set the floor for a turn played entirely from skills.
5. **Does sharing after every game make every seat converge?** §5.5 publishes each book when its
   game ends. If all seats then retrieve the same verified skills, opponents may play alike;
   whether that happens is a measurement, and the provenance on each skill is what would show
   it.
