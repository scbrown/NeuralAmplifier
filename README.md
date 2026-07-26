<p align="center">
  <img src="assets/logo.svg" width="200" alt="Neural Amplifier logo — a signal amplified through a neural core over a hex map"/>
</p>

<h1 align="center">neural amplifier</h1>

<p align="center">
  <em>🧠 An LLM brain for <a href="https://github.com/afwbkbc/glsmac">GLSMAC</a> — Alpha Centauri, played by Claude</em>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"/></a>
  <a href="https://github.com/afwbkbc/glsmac"><img src="https://img.shields.io/badge/engine-GLSMAC%20(AGPL--3.0)-orange.svg" alt="Engine: GLSMAC"/></a>
  <a href="https://github.com/casey/just"><img src="https://img.shields.io/badge/tasks-just-brightgreen.svg" alt="Task runner: just"/></a>
  <a href="VISION.md"><img src="https://img.shields.io/badge/status-pre--alpha-lightgrey.svg" alt="Status: pre-alpha"/></a>
</p>

> *The board is the signal. The model is the amplifier. Strategy is what comes out.* 🛰️

In *Sid Meier's Alpha Centauri*, a **Neural Amplifier** is a base facility that strengthens
a colony's collective mind — amplifying the will of the many into something more than the
sum of its parts. This project borrows the name and the idea: it plugs an LLM into
[GLSMAC](https://github.com/afwbkbc/glsmac), the open-source reimplementation of Alpha
Centauri, takes the raw signal of the game board, and **amplifies it into strategy** —
playing a faction on its own or advising a human at the wheel.

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
| Exists for GLSMAC today | ❌ | ❌ | ✅ |
| Reasons about long-horizon tradeoffs | ❌ | ❌ | ✅ |
| Explains *why* it made a move | ❌ | ❌ | ✅ |
| Every input fully inspectable | ❌ | ⚠️ | ✅ |
| Plays fair (fog-of-war respected) | ⚠️ (often cheats) | ⚠️ | ✅ |
| Doubles as a human copilot | ❌ | ❌ | ✅ |
| No hard-coded heuristics to maintain | ❌ | ❌ | ✅ |

GLSMAC has **no computer opponents yet** (they're on its own roadmap for ~v0.7). Classic 4X
AI leans on scripted heuristics and difficulty cheating; an LLM already understands Alpha
Centauri and can weigh fuzzy strategy the way a person does — *and say so out loud*.

## ✨ How It Works

**🧵 Thin in-game mod (`.gls.js`)**

- Hooks GLSMAC's `turn` event and snapshots the board into a compact JSON **world view**.
- Applies the orders Claude returns by calling backend bindings (`um` units, `bm` bases,
  `fm` factions, `tm` map). The engine stays authoritative — illegal orders are simply
  rejected.

**🔌 One small engine addition (C++)**

- A new **GSE HTTP builtin** so scripts can reach the outside world (GLSMAC's scripting layer
  ships no network IO by default). Invoked via `Async` so a slow model never freezes the
  render loop. Small, generic, and a candidate to contribute upstream.

**🐍 External orchestrator (Python + Claude Agent SDK)**

- Owns everything LLM-shaped: prompt assembly, tool-use loops, retries, streaming, secrets.
- Turns the world view into a prompt, calls Claude, and returns **structured, validated
  moves** — plus the reasoning behind them, for the log.

**🔍 Legible by design**

- Context leaves the game as plain JSON over HTTP, so every turn's input to Claude and every
  decision back out can be logged, replayed, and audited. No black box.

## 🏗️ Architecture

```text
        ┌──────────────────────────┐
        │   GLSMAC backend (C++)    │  authoritative game state
        └────────────┬─────────────┘
                     │ turn event / bindings
        ┌────────────┴─────────────┐
        │  agent mod  (.gls.js)     │  snapshot world view · apply orders
        └────────────┬─────────────┘
                     │ async HTTP (via GSE net builtin)
        ┌────────────┴─────────────┐
        │  orchestrator  (Python)   │  prompt · retries · validate · memory
        └────────────┬─────────────┘
                     │ Claude Agent SDK
        ┌────────────┴─────────────┐
        │          Claude           │  strategy + reasoning
        └──────────────────────────┘
```

Full design — the context Claude receives, the alternatives weighed, and the phased
roadmap — lives in **[VISION.md](VISION.md)**.

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
orchestrator/   Python service — the LLM brain (Claude Agent SDK)
mod/            .gls.js GLSMAC agent mod — the thin in-game client
engine/         C++ GSE HTTP builtin — patch + notes (AGPL-3.0 boundary)
docs/           Design docs, incl. building-and-testing.md
```

## 🛠️ Development

```bash
just build               # Build all components
just test                # Run all tests
just lint                # Lint everything
just fmt                 # Format everything
just check               # Full quality gate (pre-commit hooks)
just orchestrator test   # Component-scoped recipes: <component> <cmd>
just docs check          # Markdown lint
```

`just` is the single entry point — see [CONTRIBUTING.md](CONTRIBUTING.md). Pre-commit hooks
and CI run the same quality gate on every push. How each component is built and tested is
documented in **[docs/building-and-testing.md](docs/building-and-testing.md)**.

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and guidelines, and [AGENTS.md](AGENTS.md)
if you're an AI agent working in this repo.

## 📄 License

[MIT](LICENSE) for this repository — the Python orchestrator, the `.gls.js` mod, and the
docs.

**One boundary to know:** the GSE HTTP builtin under `engine/` modifies
[GLSMAC](https://github.com/afwbkbc/glsmac), which is **AGPL-3.0**. Any distributed
engine-side changes inherit AGPL-3.0 and are meant to be contributed upstream — a reason we
keep that surface small and separate. See [engine/README.md](engine/README.md).
