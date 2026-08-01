<p align="center">
  <img src="assets/logo.svg" width="200" alt="Neural Amplifier logo — a signal amplified through a neural core over a hex map"/>
</p>

<h1 align="center">neural amplifier</h1>

<p align="center">
  <em>🧠 An LLM brain for <em>Sid Meier's Alpha Centauri</em> — played by Claude</em>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"/></a>
  <a href="VISION.md#3-two-engines-one-brain"><img src="https://img.shields.io/badge/engines-Thinker%20%C2%B7%20GLSMAC-orange.svg" alt="Engines: Thinker and GLSMAC"/></a>
  <a href="docs/knowledge-architecture.md"><img src="https://img.shields.io/badge/knowledge-Quipu%20%C2%B7%20Hank-blueviolet.svg" alt="Knowledge: Quipu and Hank"/></a>
  <a href="https://github.com/casey/just"><img src="https://img.shields.io/badge/tasks-just-brightgreen.svg" alt="Task runner: just"/></a>
  <a href="VISION.md"><img src="https://img.shields.io/badge/status-pre--alpha-lightgrey.svg" alt="Status: pre-alpha"/></a>
</p>

> *The board is the signal. The model is the amplifier. Strategy is what comes out.* 🛰️

In *Sid Meier's Alpha Centauri*, a **Neural Amplifier** is a base facility that strengthens
a colony's collective mind — amplifying the will of the many into something more than the
sum of its parts. This project borrows the name and the idea: it plugs an LLM into a
**controllable Alpha Centauri** — the complete original game via [Thinker](https://github.com/induktio/thinker)
now, the open-source [GLSMAC](https://github.com/afwbkbc/glsmac) engine long-term — takes the
raw signal of the game board, and **amplifies it into strategy** — playing a faction on its own
or advising a human at the wheel. One brain, either engine, behind a single
[contract](docs/contract.md).

## 🎬 See It In Action

```text
turn 42 · 2142 AD · faction GAIANS  ────────────────────────────────

  world view  → 128 visible tiles, 6 units, 2 bases, research 60%
  claude      → "Ridge to the NE has +2 minerals and a defensible
                 chokepoint. Send the former to terraform, escort with
                 the scout. Hold expansion — the Hive is massing east."

  orders      ✓ move_unit former → (13,8)   [accepted]
              ✓ move_unit scout  → (14,8)   [accepted]
              ✓ set_production Gaia's Landing → Recycling Tanks
              ✓ end_turn
```

Every turn, Neural Amplifier hands Claude a compact, fog-of-war-limited picture of the game
and a menu of *legal* moves; Claude reasons about it in the open and returns orders; the game
validates and executes them. Same loop, two modes: **autonomous opponent** or **human
copilot**.

## 🤔 Why Neural Amplifier?

| | **Built-in 4X AI** | **Scripted bots** | **Neural Amplifier** |
| -- | :-----------------: | :-----------------: | :--------------------: |
| Reasons about long-horizon tradeoffs | ❌ | ❌ | ✅ |
| Explains *why* it made a move | ❌ | ❌ | ✅ |
| Every input fully inspectable | ❌ | ⚠️ | ✅ |
| Plays fair (fog-of-war respected) | ⚠️ (often cheats) | ⚠️ | ✅ |
| Doubles as a human copilot | ❌ | ❌ | ✅ |
| Grounded in the game's rules + real strategy, not hard-coded heuristics | ❌ | ❌ | ✅ |

> **On "plays fair":** that's a design commitment, not a freebie. SMAC hands non-human factions
> a systematic bonus layer — cheaper tech, free unit support, no retool penalty, no global
> warming below Transcend. Neural Amplifier keeps a
> [fairness ledger](docs/game-surface.md#5-rule-asymmetries-the-fairness-ledger) of all of them
> and **declares whichever are active** in the world view, so every result is interpretable and
> Claude reasons about its own advantages out loud. Play it on a human slot and the list is
> empty — that's the configuration behind an unqualified fair-play claim.

The original game's AI is dated and leans on difficulty cheating; the open-source engine has
**no computer opponents yet**. An LLM already understands Alpha Centauri and can weigh fuzzy,
long-horizon strategy the way a person does — *and say so out loud*. Neural Amplifier goes
further than raw training memory: it grounds that reasoning in the game's actual rules and in
curated strategy (see **Knowledge & Guardrails** below), so it plays *well*, not just legally.

## ✨ How It Works

A platform-agnostic **brain** plus thin **adapters** that attach it to a running game. The
brain never knows which game it's driving — it speaks one [contract](docs/contract.md).

**🐍 Orchestrator (Python + Claude Agent SDK) — the brain**

- Owns everything LLM-shaped: prompt assembly, tool-use loops, retries, streaming, secrets,
  memory, move validation, and safe degradation.
- Takes a **world view** in, returns **structured orders** (with reasoning) out — over HTTP.
  The same code drives either engine.

**🔌 Two engine adapters — the hands**

- **Thinker (near-term):** a fork of the MIT/C++ [Thinker](https://github.com/induktio/thinker)
  mod that bridges the original `terranx.exe`'s AI hooks. The **complete, balanced** game —
  production, tech, diplomacy, real fog — controllable on day one.
- **GLSMAC (long-term):** a `.gls.js` mod + a small GSE `http` builtin for the open-source
  engine. Clean and testable, but its game systems are still being built.

**🧠 Two tiers of decision**

- A **deterministic tier** (classic-AI heuristics, in the engine) handles the mechanical work.
  The **LLM tier** sets policy and drills down to any unit or base when it chooses — so Claude
  is consulted a few times a turn, not for every twitch.

**🔍 Legible by design**

- Context leaves the game as plain JSON over HTTP — every turn's input to Claude and every
  decision back out is logged, replayed, and audited. No black box.

## 🏗️ Architecture

```text
        ┌──────────────────────────────┐
        │   orchestrator (Python)       │  brain · MIT · platform-agnostic
        │   prompt · validate · memory  │
        └───────▲───────────────┬──────┘
         world  │               │ orders     ← the shared CONTRACT (JSON/HTTP)
          view  │               ▼
        ┌───────┴───────────────────────┐
        │            adapter             │
        │  ┌───────────┐  ┌───────────┐  │
        │  │ thinker   │  │ glsmac    │  │
        │  │ → terranx │  │ → GSE mod │  │
        │  └───────────┘  └───────────┘  │
        └───────────────────────────────┘
```

Full design — the contract Claude speaks, the two-engine strategy, and the roadmap — lives in
**[VISION.md](VISION.md)**.

## 📊 Coverage & Plan

**4 of 77 decision surfaces the brain can actually decide.** A surface is not covered until its
decision can be *applied* — observing changes what is recorded, not what the game does.
`just surfaces` prints it. The registry is frozen at 77
(`orchestrator/surfaces.py`) and partitioned by contract scope: `base` 25, `unit` 32, `turn` 20.

| Surface | Scope | Status |
| --- | --- | --- |
| `base.production` | base | Observed · action space costed in minerals, with unit roles and facility effects. `apply` closes the loop |
| `faction.tech` | turn | Observed · researchable techs with the engine's own AI valuation weights |
| `faction.se` | turn | Observed · legal (field, model) pairs with effect deltas, grounded from `#SOCIO` |
| `base.hurry` | base | Observed · credit cost and turns saved; unaffordable option omitted |

Every surface ships a **side-effect-free probe** (`observe`, `observe-tech`, `observe-se`,
`observe-hurry`) because in-game input cannot be driven at all, so a decision that fires every five
to ten turns is otherwise unverifiable without playing until it happens.

Each was **observed before it was applied** — deliberately, because that makes a surface
falsifiable on its own before anything depends on it. Which surfaces the brain may decide is set
per surface in [`surfaces.toml`](surfaces.toml), so the sequence is: instrument, watch it
observe, then let it decide.

### The plan, in dependency order

```mermaid
flowchart LR
    A["<b>1 · instrument</b><br/>emit a record per decision<br/>engine-authoritative action space<br/>+ a probe"]
    B["<b>2 · ground</b><br/>Quipu facts, id-first<br/>measure utilisation"]
    C["<b>3 · close the loop</b><br/>apply the brain's choice<br/>native answer as fallback"]
    D["<b>4 · prove it</b><br/>A/B against the<br/>deterministic tier"]

    A --> B --> C --> D

    subgraph nofb["surfaces with NO native AI path · 21 of 77"]
        E["<b>0 · build the deterministic tier first</b><br/>in Thinker, as a normal mod feature"]
    end
    E --> A

    classDef done fill:#1b5e20,stroke:#66bb6a,color:#fff
    classDef part fill:#5d4037,stroke:#ffb74d,color:#fff
    classDef todo fill:#37474f,stroke:#78909c,color:#cfd8dc,stroke-dasharray: 4 3
    class A done
    class B,C part
    class D,E todo
```

**Step 0 is the one that is easy to skip and expensive to skip.** 21 of the 77 surfaces
(`surfaces.NO_AI_PATH`) are decisions the native AI *never makes* — `base.abandon`,
`council.vote`, `base.retool`, `diplo.base_swap`. Putting an LLM straight onto those breaks
[invariant 9](AGENTS.md): *degrade safely.* There is no native choice to fall back to when the
model is slow, over budget, or wrong, so a failure there stalls or corrupts a turn rather than
quietly reverting to a competent default.

So those surfaces get the deterministic tier **first** — built in the Thinker fork as an ordinary
mod feature, the way Thinker already improves production and movement AI — and only then the LLM
tier on top of it. That also means the work is independently useful: a better deterministic tier
improves the game whether or not a brain is attached, and it gives the LLM something to be measured
*against* rather than merely compared to.

Of the remaining 52, **25 are `unit`-scope** and mostly stay deterministic on volume grounds —
see [docs/decision-inputs.md](docs/decision-inputs.md) §5 for why, and why revisiting them should
mean deciding *operations* rather than tile moves. That leaves **27 base and faction surfaces
that already have a native path and so already have a safe fallback**: the bucket to work
through. `just surfaces` prints the split from the registry.

Detail, including the seam and action-space quality per surface:
**[docs/game-surface.md](docs/game-surface.md) §2.5**. What each surface needs in its world view:
**[docs/decision-inputs.md](docs/decision-inputs.md)**.

## 🧠 Knowledge & Guardrails

Training memory knows Alpha Centauri *broadly* — but not well enough to distinguish canonical
rules from a house-rule, cite how *this* engine scores a fight, or carry strategy across games.
So the brain is backed by two sibling services (design:
**[docs/knowledge-architecture.md](docs/knowledge-architecture.md)**):

- **[Quipu](https://github.com/scbrown/quipu) — persisted, governed knowledge.** A bitemporal
  graph holding three layers: the **datalinks** (the game's rules — techs, units, facilities,
  secret projects, social engineering — as a SHACL-guarded graph, tagged so a house-rule can't
  masquerade as canonical); curated **strategy/doctrine** (real SMAC unit designs and base build
  orders — see [docs/strategy-knowledge.md](docs/strategy-knowledge.md)); and **learned memory**
  (tactics and opponent patterns the brain accumulates across games).
- **[Hank](https://github.com/scbrown/hank) — hot in-memory board + guardrail harness.** Holds
  the per-faction, fog-limited board graph in memory and runs a strategic **policy guard** and
  **what-if** analysis on proposed orders before they apply.

A third layer is the game's **own** standing intent, rather than knowledge about the game:

- **[Directives](docs/directives.md) — measurable strategy that outlives one decision.** A
  long-horizon choice (which tech path, which social model) issues a directive that later
  decisions are shown, with its current value, its priority, and what each option would cost it.
  A directive may only reference a metric the world view actually reports — so "keep energy
  reserves above 300" is expressible and checkable, and "play aggressively" is refused. They are
  *retrieved*, not broadcast: a walk out from the resource an action spends reaches the plan
  saving it, the project it is saved for, and the strategy that project serves.

  Measured on `base.hurry`, a surface that splits 6/4 across ten identical prompts with no plan:
  adding one priority-7 saving directive moved it to a **unanimous** `hurry:none`, and a second
  plan on the same observation reached **0.80**. The directive was followed on every run of both.
  That surface was never short of rules — it was short of knowing what else 81 energy credits
  were for.

Two decisions this is built to sharpen:

- **Unit design** — at the unit workshop, retrieve the doctrine's recommended **prototypes**
  (chassis · reactor · weapon · armor · abilities) for the current tech and threat, so the brain
  proposes a sound design instead of guessing.
- **Base planning** — at production, retrieve the **build order** and facility priorities for
  that base's role and game phase, so infrastructure is chosen with intent.

The engine's `action_space` stays the hard legality gate; the knowledge layer only **annotates,
constrains, and remembers** — it never invents a move. Precedence, honesty about what's blocked,
and the rollout are in the [design doc](docs/knowledge-architecture.md).

## 🚀 Quick Start

> **Status: pre-alpha, but the brain runs.** The orchestrator is real and tested — the
> contract types, `POST /decide`, action-space validation, safe degradation, the decision
> record and JSONL log, the OTel exporter, replay, the derived fairness ledger, the
> Quipu/Hank seam, and the SMAC datalinks ingester. All of it runs with **no game present**.
>
> What is *not* built: the engine adapters. Nothing plays a turn of Alpha Centauri yet.
> **Track A (Thinker) is the current focus** — the complete, balanced game, controllable
> today — and its faction gate is in the fork; intercepting a real decision is next.

```bash
git clone https://github.com/scbrown/NeuralAmplifier && cd NeuralAmplifier
just --list          # See available recipes
just setup           # Install pre-commit hooks
just check           # Run the quality gate
```

Layout:

```text
orchestrator/       Python brain — the LLM decision loop (Claude Agent SDK) · MIT
adapters/
  thinker/          Current focus: DLL bridge to terranx.exe (MIT)
  glsmac/           Long-term: .gls.js mod + GSE http builtin (AGPL boundary)
docs/               contract.md         the world-view / action-space interface
                    game-surface.md     every decision the game asks + AI coverage matrix
                    headless-harness.md game fixture + running unattended
                    observability.md    decision records, tracing, coverage
                    building-and-testing.md, adapter notes, knowledge-architecture.md,
                    strategy-knowledge.md, ontology/, Quipu/Hank integration docs
```

**Want to run a real game?** You bring your own copy of *Alpha Centauri* — see
[CONTRIBUTING.md](CONTRIBUTING.md#the-game-fixture-bring-your-own-smac). Building, linting, and
the whole test suite run with **no game present**.

**Setting up a machine or a Claude Code cloud environment?**
[`scripts/setup-environment.sh`](scripts/setup-environment.sh) installs everything —
`just`, `bd`, the 32-bit MinGW cross-compiler, Wine, and Quipu — and reports what actually
landed rather than assuming.

## 🛠️ Development

```bash
just build               # Build all components
just test                # Run all tests
just lint                # Lint everything
just check               # Full quality gate (pre-commit hooks)
just orchestrator test   # Component-scoped recipes: <component> <cmd>
just glsmac test         # GLSMAC adapter (headless --gse-tests)
just thinker build       # Thinker adapter (needs the Thinker toolchain)
just docs check          # Markdown lint, then build the book
just docs serve          # The docs site at localhost, with hot reload

just eval list           # Behavioural evals: what was measured, and what it found
just coverage            # Run health: surfaces fired, fallback rate, adherence
just replay --store …    # Re-run a recorded log against the current code — no game
just ingest              # Your alphax.txt → the smac: RDF graph + static briefing
just quipu-load          # Load that graph into a local Quipu store
just quipu-serve         # Serve it for grounded retrieval (NA_QUIPU_URL)
```

`just` is the single entry point — see [CONTRIBUTING.md](CONTRIBUTING.md). Pre-commit hooks
and CI run the same quality gate on every push. How each component is built and tested is
documented in **[docs/building-and-testing.md](docs/building-and-testing.md)**.

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the game fixture, and how to implement
against the design. If you're an AI agent working in this repo, start with
[AGENTS.md](AGENTS.md) — it has a "before you implement" doc map and the design invariants.

## 📄 License

[MIT](LICENSE) for this repository — the Python orchestrator, the Thinker adapter (Thinker is
MIT too), the `.gls.js` mod, and the docs.

**One boundary to know:** the GSE `http` builtin under `adapters/glsmac/builtin/` modifies
[GLSMAC](https://github.com/afwbkbc/glsmac), which is **AGPL-3.0**. Those engine-side changes
inherit AGPL-3.0 and are meant to be contributed upstream — a reason we keep that surface small
and separate. See [adapters/glsmac/README.md](adapters/glsmac/README.md).
