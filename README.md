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

|  | **Built-in 4X AI** | **Scripted bots** | **Neural Amplifier** |
|--|:-----------------:|:-----------------:|:--------------------:|
| Reasons about long-horizon tradeoffs | ❌ | ❌ | ✅ |
| Explains *why* it made a move | ❌ | ❌ | ✅ |
| Every input fully inspectable | ❌ | ⚠️ | ✅ |
| Plays fair (fog-of-war respected) | ⚠️ (often cheats) | ⚠️ | ✅ |
| Doubles as a human copilot | ❌ | ❌ | ✅ |
| Grounded in the game's rules + real strategy, not hard-coded heuristics | ❌ | ❌ | ✅ |

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

> **Status: pre-alpha.** The repo currently holds the vision, architecture, and project
> scaffold. The component tree below is where the code lands, phase by phase (see
> [VISION.md](VISION.md) §Roadmap).

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
  thinker/          Near-term: DLL bridge to terranx.exe (MIT)
  glsmac/           Long-term: .gls.js mod + GSE http builtin (AGPL boundary)
docs/               contract.md, adapter notes, knowledge-architecture.md,
                    strategy-knowledge.md, ontology/, and the Quipu/Hank integration docs
```

## 🛠️ Development

```bash
just build               # Build all components
just test                # Run all tests
just lint                # Lint everything
just check               # Full quality gate (pre-commit hooks)
just orchestrator test   # Component-scoped recipes: <component> <cmd>
just glsmac test         # GLSMAC adapter (headless --gse-tests)
just thinker build       # Thinker adapter (needs the Thinker toolchain)
just docs check          # Markdown lint
```

`just` is the single entry point — see [CONTRIBUTING.md](CONTRIBUTING.md). Pre-commit hooks
and CI run the same quality gate on every push. How each component is built and tested is
documented in **[docs/building-and-testing.md](docs/building-and-testing.md)**.

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and guidelines, and [AGENTS.md](AGENTS.md)
if you're an AI agent working in this repo.

## 📄 License

[MIT](LICENSE) for this repository — the Python orchestrator, the Thinker adapter (Thinker is
MIT too), the `.gls.js` mod, and the docs.

**One boundary to know:** the GSE `http` builtin under `adapters/glsmac/builtin/` modifies
[GLSMAC](https://github.com/afwbkbc/glsmac), which is **AGPL-3.0**. Those engine-side changes
inherit AGPL-3.0 and are meant to be contributed upstream — a reason we keep that surface small
and separate. See [adapters/glsmac/README.md](adapters/glsmac/README.md).
