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
|---|---|---|
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

### 5.4 Silent degradation — the failure tests miss

A run where the orchestrator timed out every single turn, fell back to `end_turn`, and finished
**completes successfully and looks green**. Every assertion about "the game ran" passes. The
brain was absent the whole time.

`degraded` makes this loud. Assert a fallback-rate ceiling in the harness (e.g. `< 5%`) and this
entire class of failure becomes impossible to ship silently. If you build one thing from this
document, build this.

### 5.5 Action-space adherence

Orders referencing an `action_id` not in the world view's `action_space` should be
**structurally impossible** — that is the anti-hallucination guarantee of VISION §4. So a
non-zero count is not a warning, it is a broken invariant. Assert exactly zero.

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
|---|---|---|
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
|---|---|---|
| **1** | Freeze the surface ID scheme in [game-surface.md](game-surface.md) | ❌ |
| **2** | Add `surface_id`, `trace`, `degraded` to [contract.md](contract.md) | ❌ |
| **3** | Decision record + JSONL writer in the orchestrator; assert in fixture tests | ❌ |
| **4** | Coverage report + fallback-rate and adherence assertions in CI | ❌ |
| **5** | Adapter stamps `surface_id` + `traceparent` | ✅ |
| **6** | OTel exporter; spans across orchestrator → Quipu → Hank | ❌ |
| **7** | Replay mode + determinism diffing in the harness | ✅ |

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
