# Observability

How we see what the brain is doing — while developing it, while testing it, and while watching
it play.

**For this project observability is not infrastructure, it is the product.** VISION §4 commits
to *"everything is inspectable — every turn's input to Claude and every decision back out is
logged, replayed, and audited. No black box."* That promise and the telemetry design are the
same artifact, so it is worth building once and properly.

Companions: [contract.md](contract.md) (the fields this rides on),
[game-surface.md](game-surface.md) (surface IDs and coverage),
[headless-harness.md](headless-harness.md) (where runs happen),
[building-and-testing.md](building-and-testing.md) (the test strategy this serves).

---

## 1. Three consumers

Build one thing for all three and it serves none. Every signal below is tagged with who it is for.

| Consumer | Question | Section |
| --- | --- | --- |
| **Developer** | why did turn 42 take 90 seconds? | §6 ops |
| **Test harness** | did this run exercise unit design, and is it still deterministic? | §5 testing |
| **Human watching** | *why did Claude do that?* | §7 legibility |

---

## 2. The decision record — the atom

One structured event per decision point. Everything else in this document is an aggregation,
a projection, or an export of this record.

```json
{
  "schema_version": "0.1",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "game_id": "gaians-2142-a3f9",
  "turn": 42,
  "year": 2142,
  "faction": "GAIANS",
  "engine": "thinker",
  "surface_id": "base.production",
  "scope": "base",
  "tier": "llm",
  "world_view_hash": "sha256:9c1185a5c5e9fc54612808977ee8f548b2258d31",
  "action_space_size": 17,
  "chosen": [{ "action_id": "a3", "action": "set_production" }],
  "reason": "Recycling Tanks — economy first.",
  "degraded": false,
  "degrade_reason": null,
  "fairness_profile": ["retool_penalty", "tech_cost_factor"],
  "model": "claude-...",
  "tokens": { "input": 8412, "output": 291, "cached": 7900 },
  "latency_ms": 1840,
  "knowledge": { "quipu_hits": 3, "hank_verdict": "allow" }
}
```

Three fields carry most of the weight:

- **`surface_id`** — the stable decision identifier from [game-surface.md](game-surface.md) §1.
  Coverage, dead-surface detection, and scenario design all key on it. **Freeze the scheme
  before the first hook emits one** — renaming later invalidates every recorded run.
- **`world_view_hash`** — content address, not the payload. The full world view is written once
  to a content-addressed store; records reference it. Keeps the stream small and makes
  "identical input?" a string comparison.
- **`degraded`** — whether this decision came from the brain or the safe fallback. See §5.4;
  this is the single most valuable field for testing.
- **`fairness_profile`** — the rule asymmetries in force, from the world view's `fairness` block
  ([game-surface.md](game-surface.md) §5). Carried on every record so a result is interpretable
  months later, and so Mode A and Mode B+ runs are directly comparable. An empty list is the
  claim "this was won under unmodified rules" — never assert that without it.

---

## 3. Two layers

**Layer 1 — JSONL, the record of truth.** Append-only `decisions.jsonl` per run, written by the
orchestrator. Zero dependencies, works offline and headless, diffable, greppable, and
replayable. Everything in §5 operates on this file. It is what a run *is*.

**Layer 2 — OpenTelemetry, the live view.** The same events fed to an OTel exporter for traces
and metrics. This earns its keep specifically because **Quipu and Hank are separate services** —
without shared trace context you cannot see that a slow turn was actually a Quipu retrieval, not
the model. Local runs can export to nothing and lose only the live view.

Both layers are fed from **one emit call**, never assembled twice. If they can disagree, they
will. The JSONL field names are deliberately OTel-shaped so the exporter is a projection rather
than a translation.

**Landed.** `telemetry.Emitter` takes an already-built record and hands *the same instance* to
every sink, so "assembled once" is structural rather than a convention — the test asserts object
identity, not field equality. `DecisionLog` is just the first sink, and it is written **first**,
so an exporter failure still leaves the record of truth on disk. Sinks never raise into the
decision loop (the same invariant #9 that governs brain failures); failures are counted on
`Emitter.failures` and surfaced at `GET /health`, because a silently dead exporter is the
observability twin of the all-fallback run. Enable layer 2 with `NA_OTEL=1` and the `otel` extra
— asking for it without the dependency raises at startup rather than serving a blind run.

---

## 4. Trace model, and where it lives

**A game is a trace.**

```text
game (trace)                         game_id, faction, engine, seed
 └─ turn 42 (span)                   turn, year
     ├─ decision base.production     surface_id, scope, tier
     │   ├─ quipu.retrieve           knowledge annotation
     │   ├─ llm.call                 model, tokens, latency
     │   └─ hank.policy_guard        allow / warn / deny
     ├─ decision unit.design
     └─ decision faction.tech
```

**The orchestrator owns telemetry export; the adapter owns almost nothing.** This follows
directly from invariant #3 (*keep the adapter thin*) and from a hard constraint: the Thinker
adapter is a DLL inside a 32-bit `terranx.exe` under Wine, and **must not block the message
pump** ([thinker-adapter-notes.md](thinker-adapter-notes.md) §5). Synchronous telemetry egress
from inside the game process is not an option.

So the adapter's entire telemetry job is: **stamp a few fields on the world view it already
sends.** The orchestrator does the rest.

### Contract delta

All optional and additive — an adapter that omits them still works, it just cannot be measured:

- **World view** gains `surface_id` (which decision this is), `trace` (`{ "traceparent": … }`,
  W3C format), and `fairness` (the declared rule asymmetries in force — see
  [game-surface.md](game-surface.md) §5). The adapter is the **root** of the trace because the
  game is the root of the causality; the orchestrator continues the context rather than starting
  a new one.
- **Orders** gain `degraded` (bool) so the adapter can log locally that it applied a fallback
  rather than a real decision.

`fairness` is the adapter's job because only the engine side knows `is_human`, `*DiffLevel`, and
the `conf.*` values that determine which handicaps are live.

Recorded in [contract.md](contract.md).

### Correlating with the game itself

`mod_auto_save` already writes `saves/auto/Autosave_<year>.sav` every turn
([headless-harness.md](headless-harness.md) §3.1). Tagging each decision record with `turn` and
`year` means **every decision is correlatable to a real savegame** — so "what did the board
actually look like when it made that call?" is answerable by loading the save, not by trusting
the log. Free ground truth; design for it now.

---

## 5. Testing leverage

The strongest argument for building this early: **telemetry is the assertion surface.** Tests
query the decision stream rather than needing bespoke hooks.

### 5.1 Coverage

Surfaces fired, with counts, per run. Turns [game-surface.md](game-surface.md) from a design
document into a measurement:

- *Did this scenario exercise unit design?* → assert `unit.design` count > 0
- *Are we regressing?* → diff surface sets between runs
- *Is a surface dead?* → implemented but never fires; the scenario is wrong or the hook is misplaced

It also makes canned-save design deliberate — a coastal start for naval surfaces, a
contact-heavy save for diplomacy — each targeting **named** surfaces.

### 5.2 Determinism by diffing

Two runs of the same canned save with the same scripted decisions should emit identical
streams (modulo `latency_ms` and timestamps). Diff them. That answers the open Wine-determinism
question with no purpose-built harness — nondeterminism shows up as the first differing record,
which also tells you *where*.

### 5.3 Replay as regression

Because every input is pure JSON and content-addressed, a recorded game can be replayed against
a changed orchestrator with **no game running**: feed the stored world views back in, diff the
decisions. Changed decisions are either the improvement you intended or the regression you
didn't. This is only possible because the contract is plain JSON over HTTP — it is a payoff of
that design choice, so don't spend it.

**"The stored world views" needed somewhere to be stored.** The decision record is
content-addressed but does not *contain* its input — it carries `world_view_hash` and nothing
else — so a log on its own cannot be replayed at all. `replay.WorldViewStore` keeps the bytes,
keyed by the same hash the record already carries, which means a record and its input can never
be mismatched and repeated inputs cost one file. Enable it with `NA_WORLD_VIEW_STORE`; a run
without it produces a log that is readable but not replayable, and `neural-amplifier replay`
says so rather than reporting a clean pass over zero decisions.

Two verdicts, because the run kinds support different claims. A scripted run must match exactly
(`--exact`). A **real-model** run can only be held to the weaker one: nothing newly degraded and
the same surfaces fired. The trap that motivates keeping them apart is a replay where the
fallback happens to choose what the brain chose — the actions match, every "did it decide?"
assertion passes, and the brain is gone. `new_degradations` catches it; a bare action diff does
not.

The store doubles as **fixture harvesting** (§5 of [headless-harness.md](headless-harness.md)):
one recorded game yields orchestrator fixtures no hand-written world view would.

**A repaired decision has two inputs, and the second one used to be lost.** When every choice is
thrown out, the brain is re-asked from the original world view with the reason appended as
advisories. The store write happens once, before that loop, so `world_view_hash` addresses the
first prompt and cannot address the second — the view the brain actually answered from existed
for the length of one call and nothing held the bytes. Each re-asked view is now stored too and
its hash recorded in `repair_inputs`, alongside a `repairs` count that is the field to read when
no store is configured. A successful repair was otherwise invisible on the record: the degrade
reason names repair attempts only when they *failed*, so a decision that took two round trips
read exactly like one that landed first time, and that difference is a turn the game spent
waiting.

`world_view_hash` deliberately does **not** widen to the last prompt. A replay starts from the
original and regenerates its own advisories, so a changed guard producing a different second
prompt surfaces as a divergence — which is the entire job — rather than being hidden by replaying
the recorded prompt back at the brain. `repair_inputs` answers the other question, the forensic
one: what did the brain read when it answered this way.

### 5.4 Silent degradation — the failure tests miss

A run where the orchestrator timed out every single turn, fell back to `end_turn`, and finished
**completes successfully and looks green**. Every assertion about "the game ran" passes. The
brain was absent the whole time.

`degraded` makes this loud. Assert a fallback-rate ceiling in the harness (e.g. `< 5%`) and this
entire class of failure becomes impossible to ship silently. If you build one thing from this
document, build this.

#### The inverse: `degraded: false` on a decision the game abandoned (na-t3h)

The paragraph above assumes `degraded` is *reachable* — that the orchestrator finds out when a
decision failed to land. It did not, and the resulting run was worse than the one above, because
it looked green from both ends and the two ends did not agree.

The thinker adapter blocks for `conf.llm_timeout_ms` (2500 by default), then applies the
deterministic tier's pick and moves on. The orchestrator's agent brain waited on
`NA_AGENT_TIMEOUT`, which is unset by default and means *wait forever*. Nothing carried the
adapter's deadline across, so an agent answering minutes later ran a full decision loop for a
turn resolved long before — and wrote a record saying `tier=llm, degraded=false`, while
`/agent/submit` replied `"applied to the game"`.

Measured over one live run: **66 adapter rows in `na-observations.jsonl`, zero with
`tier=llm`** — every one `applied=native`, `fallback_reason="orchestrator unreachable or slow"`
— against orchestrator records claiming applied llm decisions for the same turns.

Three properties of this failure are worth naming, because each one defeats a check that
normally works:

- **The fallback-rate ceiling passes.** The orchestrator's degrade rate was ~0. It was measuring
  its own decision loop, which completed every time.
- **Neither log is malformed.** Both are internally consistent. The disagreement is only visible
  by joining them, which nothing did.
- **The agent cannot detect it.** It is told its choice was applied, so its next turn reasons
  from a board state that never existed.

The fix is the same shape as §5.5.1: ask the party that actually knows. `decision_deadline_ms`
(contract.md) is the adapter stating how long it will still be listening; `AgentBrain` waits on
the tighter of that and its own timeout, minus a margin, so **the orchestrator gives up first,
always and deliberately**. The decision then degrades honestly, and a late `/agent/submit` is
refused with 409 naming the deadline rather than recorded as applied.

The general rule this leaves behind: **a `degraded` flag can only be trusted about the process
that sets it.** `degrade.rate` measures whether the *orchestrator* reached a brain. Whether a
decision reached the *game* is an adapter-side fact (`tier` in `na-observations.jsonl`), and any
claim about it made from inside the orchestrator is inference. `/agent/submit` no longer says
"applied to the game" for that reason — it says what this process did and stops there.

#### Read `degrade.rate` per surface, or a dead surface hides in a live average (na-co2)

A third shape, and the one the ceiling in §5.4 is weakest against. `StateGuard` read
`minerals_remaining` — a **shortfall**, minerals still owed — as if it were a spendable budget,
and every build option costs more than the shortfall *by construction*. So on any base that had
banked a single mineral, the guard denied the **entire** action space, the repair ask had nothing
legal left to return, and the decision fell to the deterministic tier:

```text
degraded=true  degrade_reason="guard denied every choice (1 stripped); 1 repair attempt(s) also failed"
```

Why the usual checks did not fire:

- **Nothing looks broken.** The deterministic fallback returns a legal item — in the measured
  case the *same* item the agent had chosen. The game plays on; the run has simply stopped being
  an llm-tier run.
- **A fleet-wide ceiling absorbs it.** `base.production` was degrading at near 1.0 on every
  developed base while `faction.se` and `faction.tech` were fine. Averaged across surfaces the
  number stays under a 5% ceiling for a long time.
- **It is worst on the decision most worth having.** The textbook case is continuing a
  nearly-finished item — 27 of 33 minerals banked, one turn to go — which is exactly what the
  history work and the "prefer continuity" guidance exist to get right.

So: **break `degrade.rate` out by `surface_id`, and alert on a surface at a floor of zero
llm-tier decisions rather than on the aggregate.** The measurable tell that named this bug was
not a rate at all — it was that `base.production` had no `tier=llm` / `applied=llm` row in
`na-observations.jsonl` after 42 turns while `faction.se` had four. A surface that has *never*
landed a brain decision is a stronger signal than any average, and it is free to compute.

#### The half a deadline cannot reach: the game process that is gone (na-bzd)

`decision_deadline_ms` fixes the case above by making the orchestrator race a clock the engine
declared. That only works while the engine is **alive to reach it**. Nothing on the orchestrator
side counts down once a decision loop is already blocked — the expiry is the adapter's socket read
giving up — so a deadline stated by a process that has since been killed is never reached by
anything at all.

Measured 2026-08-02. A game was killed mid-decision and relaunched; the still-running
orchestrator's `/agent/waiting` offered **four decisions at turn 40, status `pending`, ages
600–1275 s**, every one raised by a process dead for twenty minutes. Claiming and answering one
returned the ordinary success response with `degraded: false`. They were indistinguishable from
live work in the queue, in `/agent/waiting`, and to an agent polling `/agent/next`.

This is the §5.4 family again with the detector one layer further out. The orchestrator's record
would have been internally consistent, the adapter would have written no contradicting row at all
(there was no adapter), and the only observable was an age — which is exactly the signal that
cannot be trusted here, because a legitimately slow agent looks identical to a dead game and this
project deliberately supports agents that think for minutes.

The fix is the same rule a third time: **ask the party that actually knows.** `run_id`
(contract.md) is the adapter naming its own process. A `POST /decide` bearing a run id different
from the one the queue has been seeing is the only evidence the orchestrator will ever get that
the process it was serving has exited, and on that evidence every decision from the older run is
retired — released with a degraded record naming the restart, removed from `/agent/waiting` and
`/agent/next`, and refused on `/agent/submit` with a 409 that says the game that raised it is gone
rather than a bare conflict.

Two readings are deliberately **not** destructive, because absence is not evidence: a world view
with no `run_id` changes nothing (every adapter is absent until it is upgraded), and the *first*
run id an orchestrator sees is adopted without retiring anything (a first sighting is not a
restart). The failure those rules avoid is the mirror one — abandoning decisions a healthy game is
still blocked on, which would read in a log exactly like this bug and hit adapters doing nothing
wrong.

The record shape is unchanged; what is new is a `degrade_reason` that names a process death. A run
with several of those is a run whose game was restarted, and that is now readable from the decision
log alone instead of being invisible.

> **Retired operational note.** Until this landed, the standing advice was to restart the
> orchestrator whenever the game restarted and to confirm `/agent/waiting` was empty before
> trusting the queue. That is no longer necessary, and the reason it is worth recording that it
> ever was: the workaround cost real time twice during one run, because stale pendings looked
> like live work and nothing in the interface said otherwise.

### 5.5 Action-space adherence

Orders referencing an `action_id` not in the world view's `action_space` should be
**structurally impossible** — that is the anti-hallucination guarantee of VISION §4. So a
non-zero count is not a warning, it is a broken invariant. Assert exactly zero.

**`repeated_actions` is the other half, and it is not the same number.** `validate()` also drops
a choice naming an action already chosen in the same answer. That is legal — the id *was*
offered — so it must not raise `adherence_violations`, or the one metric that means "broken
invariant" starts firing on a brain that is merely sloppy. It gets its own count on the record
and its own line in the coverage summary, with no ceiling to assert: a repeat costs nothing on
its own, and a brain repeating one id fifty times is a model looping, which without this reads
exactly like a clean one-choice turn. Both counts accumulate across repair attempts, so a second
attempt that came back clean does not erase what the first one did.

### 5.5.1 Divergence — the check that covers rules nobody encoded

Adherence (§5.5) and the adapter's legality gates test **what we thought to check**: the id
parses, `tech_avail` says yes, `society_avail` says yes, the faction can afford it. Every one of
those is a rule someone wrote down. None of them can catch a rule nobody wrote down — an engine
path that overwrites `queue_items[0]` after `mod_base_change`, a retool interaction, a later hook
with its own opinion.

That failure is silent in the worst way. The decision record says `"applied":"llm"` with the
chosen item, the base builds something else, and **nothing anywhere disagrees** — the log is
internally consistent and wrong.

So the adapter asks a different question after the apply: not *should* the engine accept this,
but *did* it. `na_verify_base_production` reads `queue_items[0]` back and compares it against
what was decided. It needs no theory of why a choice was dropped, which is the entire point —
it is the only mechanism here that covers rules we have not learned yet.

A disagreement emits its own compact record into `na-observations.jsonl`:

```json
{"surface_id":"base.production","event":"divergence","turn":42,"base_id":0,
 "intended_item":-4,"intended_item_name":"Recycling Tanks",
 "applied_item":12,"applied_item_name":"Scout Patrol",
 "fallback_reason":"engine did not keep the applied item"}
```

Three properties, each load-bearing and each pinned in `test_adapter_contract.py`:

- **Both items, always.** "The engine dropped our choice" cannot be investigated without knowing
  what it dropped it *for*, and the applied item alone is indistinguishable from an ordinary
  deterministic decision.
- **No `tier`, no `applied`.** A divergence is not a decision the LLM tier made or declined to
  make. Folding it into either count moves a number that measures something else.
- **No claimed cause.** The reason says what was observed. Naming a mechanism we have not
  established is how a guess becomes a fact in someone's analysis three months later.

Reported once per base-turn, not once per call: `mod_base_reset` is hooked at eleven call sites,
so the cache is updated to what the engine actually holds and the remaining calls agree.

**A non-zero divergence count is a bug in the adapter, not a bad model day.** Unlike
`degrade_rate` there is no acceptable floor — the model was never involved.

### 5.5.2 The audit — finding it before a decision rides on it

§5.5.1 is reactive: it notices a dropped choice after one has already cost a decision, in a real
game, once. The `audit <faction_id>` command is the proactive half — walk the entire action
space and find the options that *would* be refused, before anything depends on one.

It applies nothing. Attempting each option would spend credits, retarget research and hurry
production, so the audit would cost more than the bug it was looking for — and it would still
only ever test whichever option the engine accepted first. Every check is a predicate, which is
what makes auditing *every* option affordable and total rather than expensive and partial.

For `base.production` and `faction.tech` the action space and the apply gate call the same
engine function, so comparing them is a tautology. What is **not** a tautology is the trip in
between: the id is formatted into a string by the emitter and read back by the parser, and those
are separate code. A facility negation that flips sign, an off-by-one bound, a range the parser
rejects — all of that lives between two identical predicates and shows up in neither. The audit
walks `emitter → string → parser → gate`, which is the path a real answer takes.

**`faction.se` is the reason this exists.** Its action space hand-rolls three exclusions — the
model in force, an unresearched prerequisite, the faction's forbidden value — while its gate
binds on `society_avail`. §2.5 of [game-surface.md](game-surface.md) says those "are supposed to
agree." Nothing checked it. The two directions are reported separately because they are opposite
defects:

| Field | Meaning | Severity |
| --- | --- | --- |
| `rejected` | offered, but the gate would refuse it | an illegal move waiting to happen |
| `hidden` | the gate would accept it, but it was never offered | a legal option the brain cannot choose |

Mismatching ids are listed, not just counted — capped, with the record stating when the cap bit,
because a truncated list that looks complete is worse than an obviously truncated one.

`base.hurry` is deliberately **not** audited, and that is the more useful lesson. The first
version compared two identical expressions: a check that can never fail, which reads as coverage
while testing nothing. The real defect was that what may be rushed and what it costs had been
derived independently in three places — and not even the same way, one measuring from the
caller's `minerals_before` and another from the live `minerals_accumulated`. Auditing for that
drift was the wrong instinct, since a detector only fires *after* someone introduces the bug.
Deriving it once (`na_hurry_terms`) means the two cannot disagree and there is nothing left to
check. **Prefer the version with no failure mode over the version with a detector.**

### 5.6 Fixture harvesting

Because records reference content-addressed world views, **every run automatically produces the
fixture corpus** for the fast orchestrator lane. Telemetry and fixture harvesting are one
pipeline, not two — which is what makes the slow game lane worth its cost
([headless-harness.md](headless-harness.md) §5).

---

## 6. Ops signals

RED (rate / errors / duration) plus the domain-specific ones. Two of these answer questions
VISION §2 explicitly lists as **honest unknowns** — they stop being unknowns once the record exists.

| Signal | Why | VISION link |
| --- | --- | --- |
| `decision.latency` p50/p95 per surface | Is the model fast enough for turn pace? | **named unknown** |
| `tokens.per_turn`, `cost.per_game` | Is this economically viable? | **named unknown** |
| `degrade.rate` | Is the brain actually present? | §5.4 |
| `cache.hit_rate` | Static briefing should be prompt-cached across a game | — |
| `action_space.size` distribution | Prompt bloat; drives drill-down policy | — |
| `tier.split` (deterministic vs LLM) | Are we consulting Claude a few times a turn, as designed? | VISION §4 |
| `quipu.latency`, `hank.latency` | Knowledge layer must degrade safely, not stall | knowledge-architecture |
| `turn.wall_clock` | The number a human actually feels | — |

The `tier.split` metric is worth calling out: VISION's two-tier model promises Claude is
consulted "a few times a turn, not for every twitch." That is a falsifiable claim, and this
metric falsifies it.

The exporter emits `na.decision.latency`, `na.decision.count`, `na.action_space.size`, and
`na.tokens`, dimensioned by `surface_id`, `scope`, `tier`, `engine`, and `degraded` — so
`degrade.rate`, `tier.split`, and `cache.hit_rate` are all queries over those rather than
separate instruments. Metric attributes are deliberately a **subset** of span attributes:
`game_id`, `turn`, and `world_view_hash` identify a single decision and would give every decision
its own time series, so they ride on the span only. Degradation is also set as span **status**,
which makes an all-fallback run read as red in any tracing UI without anyone writing a query.

---

## 7. Legibility — the product surface

Same stream, different projection. The decision record already contains `reason`, the world-view
reference, the action space, and the knowledge annotations — which is everything needed to answer
*why did Claude do that?*

- **Per-turn reasoning trail** — the ordered decisions for a turn with their reasons, and what
  each was chosen *instead of* (the rest of `action_space` is right there).
- **Provenance** — `knowledge.quipu_hits` and `hank_verdict` show whether a move came from
  training memory, governed doctrine, or learned tactics, and whether the policy guard modified
  it. This is the honesty half of the knowledge architecture: it makes "the brain was told this"
  distinguishable from "the brain assumed this."
- **Copilot mode** consumes exactly this to render advice in-game — via the custom dialog path in
  [headless-harness.md](headless-harness.md) §4.3, with `{$MSG0}` carrying the reasoning.
- **Audit** — because the world view is content-addressed and the savegame is correlatable, any
  claim about a past decision is checkable rather than merely logged.

---

## 8. Sequencing

Each step is useful on its own and none requires a running game until the last.

| Step | What | Game? |
| --- | --- | --- |
| **1** | Freeze the surface ID scheme in [game-surface.md](game-surface.md) | ❌ |
| **2** | Add `surface_id`, `trace`, `degraded` to [contract.md](contract.md) | ❌ |
| **3** | Decision record + JSONL writer in the orchestrator; assert in fixture tests | ❌ |
| **4** | Coverage report + fallback-rate and adherence assertions in CI | ❌ |
| **5** | Adapter stamps `surface_id` + `traceparent` | ✅ |
| **6** | OTel exporter; spans across orchestrator → Quipu → Hank | ❌ |
| **7** | Replay mode + determinism diffing in the harness | ✅ |

Steps 1–4, 6, and the game-free half of 7 have **landed** in the orchestrator; 5 and a real
recorded game wait on the adapter and the harness.
Step 6's span-per-decision, the §6 metrics, and W3C context continuation are all tested against
an in-memory tracer, so the exporter is verified with no collector and no game.

Steps 1–4 land the entire testing payload **with no game and no adapter** — they are orchestrator
work, they run in existing CI, and they are the cheapest high-value work available right now.

---

## 9. Open questions

1. **Surface ID naming.** `base.production` vs `thinker.base.production` — does the ID encode the
   engine? Argues no: the same decision exists on both engines and coverage should compare across
   them. Argues yes: some surfaces will only ever exist on one.
2. **World-view store.** Content-addressed files on disk are obvious for local runs; unclear what
   happens for a long game or a shared corpus. Retention policy unset.
3. **Record volume.** One record per decision per turn, with drill-down, could be thousands per
   game. Sampling would break coverage and determinism diffing — so probably no sampling, but the
   size needs measuring.
4. **Redaction.** Records contain full reasoning text. Fine locally; needs a decision before any
   run is published or shared as a fixture.
5. **Does the adapter need a local log at all?** `degraded` suggests yes for post-mortems when the
   orchestrator was unreachable — but that is exactly when it cannot report. A tiny local file may
   be the only witness to a total-failure run.
6. **Determinism scope.** Model output is not deterministic even at temperature 0. Determinism
   diffing therefore applies to *scripted/fake-Claude* runs; real-model runs need a weaker
   assertion (same surfaces fired, same legality) rather than identical decisions.
