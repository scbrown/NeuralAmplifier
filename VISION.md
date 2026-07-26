# Neural Amplifier — Vision

> An LLM brain for *Sid Meier's Alpha Centauri*. Neural Amplifier plugs Claude into the game
> so it can play a faction on its own **or** copilot a human — reasoning about strategy the
> way a person would, with every decision and every piece of context it saw fully
> inspectable.

**Status:** concept / pre-alpha. This document is the north star and the architecture we're
building toward, written honestly about what exists today versus what we'll add. Engine-level
specifics are grounded in source and kept in the adapter notes
([GLSMAC](glsmac-integration-notes.md), [Thinker](thinker-adapter-notes.md)).

---

## 1. What it is

Neural Amplifier is an **LLM decision brain** for Alpha Centauri, plus thin **adapters** that
attach it to a running game. Each turn, an adapter hands Claude a compact, fog-limited
**world view** and a menu of **legal actions**; Claude reasons in the open and returns orders;
the game validates and executes them. Two modes over one loop:

- **Autonomous** — Neural Amplifier *is* the computer opponent, playing a faction end to end.
- **Copilot** — it advises a human, proposing and annotating moves the player approves.

The name is the pitch — and a nod to the Alpha Centauri base facility of the same name, which
amplifies a colony's collective mind. Neural Amplifier takes the raw signal of the board and
amplifies it into strategy.

## 2. Why now

1. **LLMs are unexpectedly good at exactly what 4X AI has always hard-coded badly** — fuzzy,
   long-horizon strategic tradeoffs, weighed in natural language and *explained*.
2. **Claude already knows Alpha Centauri broadly** — factions, tech tree, social engineering,
   terraforming, secret projects, combat — from training. We feed *state*, **plus a governed
   knowledge layer**: broad memory isn't enough to play *well*, so a Quipu-backed knowledge base
   grounds engine-specific mechanics, keeps a house-rule from masquerading as canonical, and
   accumulates learned strategy across games (see
   [docs/knowledge-architecture.md](docs/knowledge-architecture.md)).
3. **The tooling to do this cleanly now exists** — an agent SDK for the brain, and two viable
   engines to attach it to (below).

Honest unknowns: LLM latency vs. turn pace, per-turn token cost, and whether the model plays
*well* rather than merely legally. This is a bet worth testing.

## 3. Two engines, one brain

There are two ways to get a controllable Alpha Centauri, with opposite tradeoffs — so we
target **both**, behind one shared contract.

| | **Original `terranx.exe` (via Thinker)** | **GLSMAC** |
|---|---|---|
| The game | **Complete & balanced** — production, full tech tree, SE, diplomacy, combat, secret projects, all factions | **Early** — units, bases, tiles, turns exist; production/tech/diplomacy/fog **not built yet** |
| Attach point | Fork [Thinker](https://github.com/induktio/thinker) (**MIT, C++**); bridge its AI decision hooks | Add a GSE `http` builtin + a `.gls.js` mod (AGPL boundary) |
| Payoff | **Fast** — a real, deep game to control immediately | Slow — must build game systems first |
| Cost | 32-bit Windows binary; Wine/VM; hard to test/headless | Clean, open, cross-platform, **testable headless** |
| Role | **Near-term** proving ground | **Long-term** open platform |

**The key insight: the brain is platform-agnostic.** It only needs a *world view* in and an
*action space* out. So we design **one contract** ([docs/contract.md](contract.md)) and write
two thin adapters that speak it — nothing in the orchestrator is wasted when we switch or
support both.

## 4. Design principles

- **The game stays authoritative.** The LLM *proposes*; the engine *validates and executes*.
  Claude picks from an engine-supplied action space, so an illegal/hallucinated order is
  rejected, never enacted. (In GLSMAC this falls out of its event `validate/apply/rollback`
  model; in Thinker, from its AI hook returning a legal choice.)
- **Two tiers of decision.** A **deterministic tier** (classic-AI heuristics, in the engine)
  handles the mechanical, high-frequency work — pathfinding, former/terraforming, base
  governors, default production. The **LLM tier** sets policy and *drills down* to take direct
  control of any unit or base when it chooses. Cheap, fast, testable; the LLM is consulted a
  few times a turn, not for every twitch.
- **One contract, two adapters.** All engine specifics live in the adapter; the orchestrator
  never knows which game it's driving.
- **Never block the game.** Decision round-trips are async (GSE `Async`/`Accumulate` in
  GLSMAC; a non-blocking hook in Thinker). A slow model degrades turn latency, never freezes.
- **Graceful degradation.** Model slow/over-budget/unreachable → safe fallback (hold /
  end turn / deterministic default).
- **No cheating.** The world view is fog-limited to what the faction legitimately sees.
  (Thinker has real fog; GLSMAC has none yet — see §6.)
- **Everything is inspectable.** Context leaves the game as plain JSON; every turn's input to
  Claude and every decision back out is logged and replayable. No black box.

## 5. Architecture

```text
      ┌─────────────────────────────────────────────┐
      │        Neural Amplifier orchestrator         │   Python · Claude Agent SDK · MIT
      │   prompt · tool-loop · retries · memory ·    │   platform-agnostic
      │   validate moves · degrade safely            │
      └───────────────▲───────────────┬─────────────┘
                      │  world view   │  legal orders     ← the shared CONTRACT (JSON)
      ┌───────────────┴───────────────▼─────────────┐
      │                  adapter                     │
      │   ┌────────────────┐   ┌──────────────────┐  │
      │   │ thinker (MIT)  │   │ glsmac (AGPL)    │  │
      │   │ DLL hook →     │   │ .gls.js mod +    │  │
      │   │ terranx.exe    │   │ GSE http builtin │  │
      │   └────────────────┘   └──────────────────┘  │
      └──────────────────────────────────────────────┘
              deterministic tier lives here, per engine
```

- **Orchestrator (Python + Claude Agent SDK, MIT).** Owns everything LLM-shaped: prompt
  assembly, tool-use loops, retries, streaming, secrets, memory, move validation, and safe
  degradation. Speaks only the contract.
- **Contract (JSON over HTTP).** The versioned world-view and action-space schema both
  adapters implement. See [docs/contract.md](contract.md).
- **Thinker adapter (MIT).** A fork of Thinker that intercepts its production/movement AI
  hooks, serializes the game's real state to the contract, and applies Claude's choices.
  Near-term, deep game. See [docs/thinker-adapter-notes.md](thinker-adapter-notes.md).
- **GLSMAC adapter (AGPL boundary).** A `.gls.js` mod that snapshots state and applies orders
  as GSE events, plus a small GSE `http` builtin so it can reach the orchestrator. Long-term,
  open. See [docs/glsmac-integration-notes.md](glsmac-integration-notes.md).

## 6. What Claude sees — the world-view contract

The heart of the project, and meant to be looked at. **The best part of an LLM brain is that
its entire input is legible.**

Layers of context (full detail in [docs/contract.md](contract.md)):

1. **Static briefing** (once/game, prompt-cached): rules/house-rules, faction roster +
   agendas, victory conditions, map size, difficulty — sourced from the **Quipu datalinks KB**,
   engine-filtered so only rules true for the current engine appear.
2. **Per-turn world view** (fog-limited JSON): turn/year/scores; tiles (terrain, altitude,
   resources, features, improvements, owner); own units (id, type, position, HP, morale,
   moves, orders); bases (location, pop, yields, production, facilities, garrison); economy
   (energy, research, SE, techs); visible others; and **deltas since last turn**.
3. **Action space**: the legal moves this turn — the engine-accepted menu Claude selects from
   (anti-hallucination).
4. **Memory & knowledge**: turn-to-turn notes plus retrieved facts, tactics, and opponent
   patterns from the **Quipu knowledge layer**. Strategic memory is a governed *bitemporal*
   store (learned across games, time-travelable), not just a free-text string.
5. **Guardrails**: the engine's `action_space` (hard legality) plus a **Hank policy harness**
   that checks proposed orders against governed strategic/house-rule invariants before they
   apply. See [docs/knowledge-architecture.md](docs/knowledge-architecture.md).

**Grounded reality (differs by engine):**

- **Thinker/`terranx`** already has the *full* state and real **fog-of-war** — the world view
  can be rich and fair from day one.
- **GLSMAC** exposes units/bases/tiles/factions but **no production, tech, diplomacy, or
  fog** yet, and returns **full ground truth** (no per-faction visibility). So on GLSMAC the
  early world view is thinner and "unfair" until we add those systems. The contract is
  **versioned** to grow with each engine.

## 7. Roadmap

Dual-track, sharing the orchestrator. Each phase names an exit criterion.

**Track A — Thinker adapter (near-term payoff)**

- **A0 — Spike:** build the Thinker fork; log one AI decision hook (e.g. base production) with
  the game state at that moment. *Exit:* we can observe a real decision point.
- **A1 — Bridge:** POST that state to the orchestrator, return a choice, apply it. *Exit:* one
  production/movement decision made by Claude in a real game.
- **A2 — Faction:** route the full AI decision surface; Claude plays a faction. *Exit:* a
  complete game with an LLM-driven faction.

**Track B — GLSMAC adapter (long-term platform)**

- **B0 — Enablers (fork work):** GSE `http` builtin; extend GSE test mocks to game bindings;
  headless mode via the Null subsystems. *Exit:* a `.gls.js` mod round-trips a move to the
  orchestrator, testable headless.
- **B1 — Action layer:** unit orders (move cost/legality, goto), base production, scriptable
  non-human slots — as GSE events. *Exit:* a real, growing action space.
- **B2 — Fog & depth:** per-faction visibility, then tech/SE/diplomacy; contribute upstream.
  *Exit:* a fair world view and a game worth playing well.

**Shared — Orchestrator (both tracks)**

- **S1 — Contract + brain:** the world-view/action schema, prompt pipeline, fake-Claude test
  harness, move validation, memory — all testable with **no game** (fixtures). This is the
  bulk of our tests and the first thing we build.
- **S2 — Two tiers:** deterministic defaults + LLM policy/drill-down.
- **S3 — Copilot:** human-in-the-loop suggest/approve.

**Knowledge & guardrails — Quipu + Hank (both tracks)**

The governed knowledge layer, sequenced in [docs/knowledge-architecture.md](docs/knowledge-architecture.md).

- **K1–K3 — Quipu:** datalinks read path (parse `alphax.txt` → SHACL-governed `smac:` facts →
  Quipu-sourced static briefing), per-turn retrieval, then bitemporal learned memory (a tactic
  learned in game N surfaces in game N+1). *Exit: prompts are annotated from a governed KB and
  memory carries across games.*
- **K4–K6 — Hank:** engine-mechanics grounding (promote engine scoring functions into Quipu),
  dev-time code guardrails, then the hot in-memory game-state graph + policy-guard + what-if
  harness. *Blocked-partial:* depends on Hank Phase 4 and net-new non-code ingestion.

## 8. Prior art for the deterministic tier

GLSMAC has no AI; SMAC's is dated. Adapt proven work rather than inventing:

- **[Thinker](https://github.com/induktio/thinker)** (MIT, C++) — the modern gold-standard
  SMAC AI overhaul: production/movement AI, former automation, base management. License-
  compatible to *adapt* (with attribution); it's also our near-term engine.
- **[Freeciv](https://github.com/freeciv/freeciv)** AI (GPL) — well-structured open 4X AI
  (autoworkers, city governors, threat models) for *design inspiration*.
- **SMACX AI Growth mod** (Yitzi) — rules/data-level (`alphax.txt`) heuristic *values*.

Those same `alphax.txt` values seed the **Quipu datalinks KB**, and **Hank** promotes the
engines' actual scoring functions into it — so the knowledge layer is grounded in the
deterministic tier's own logic, not a paraphrase.

## 9. Open questions & risks

- **GLSMAC is early.** Most of the game's systems don't exist; Track B is partly game
  development. Track A mitigates by giving a complete game now.
- **Thinker is a 32-bit Windows binary.** Wine/VM to run; harder to test and automate; can
  distribute only the DLL+patcher (users bring their own game).
- **Latency & cost** per turn — mitigated by the deterministic tier, caching, deltas, tiered
  models.
- **Play quality** — legal ≠ good; only observation tells. This is the real experiment.
- **License boundaries** — orchestrator and Thinker adapter MIT; the GLSMAC GSE builtin is
  AGPL-3.0 and upstream-bound. Keep engine surfaces small.

## 10. References & glossary

- **Thinker** — <https://github.com/induktio/thinker> (MIT). Near-term engine + AI reference.
- **GLSMAC** — <https://github.com/afwbkbc/glsmac> (AGPL-3.0). Long-term open engine.
- **GSE** — GLSMAC Scripting Engine; the `.gls.js` interpreter (`src/gse/`).
- **Contract** — the platform-agnostic world-view + action-space JSON the orchestrator speaks.
- **Adapter** — thin engine-side code translating a game to/from the contract.
- **Deterministic tier / LLM tier** — mechanical heuristics vs. LLM policy + drill-down.
- **SMAC** — *Sid Meier's Alpha Centauri* (+ *Alien Crossfire*).
