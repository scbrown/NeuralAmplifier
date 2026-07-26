# Neural Amplifier

**An LLM brain for [GLSMAC](https://github.com/afwbkbc/glsmac).** Neural Amplifier plugs
Claude into the open-source *Alpha Centauri* engine so it can play a faction autonomously —
or ride along as a copilot for a human player — reasoning about strategy in natural language,
with every decision and every piece of game state it saw fully inspectable.

> **Status:** concept / pre-alpha. No code yet — this repo currently holds the vision and
> architecture we're building toward.

## The idea in one loop

Each turn, a thin GLSMAC mod snapshots the board into a compact, fog-of-war-limited JSON
"world view" and a menu of legal moves, sends it to an external Claude orchestrator, and
applies the orders Claude sends back. The game stays authoritative — the LLM proposes, the
engine validates and executes. It runs the same way for an autonomous computer faction or a
human's copilot.

## Read the vision

**→ [VISION.md](./VISION.md)** — the full picture: architecture (the "script-first bridge"),
exactly what context Claude receives, the alternatives we weighed, and the phased roadmap
from first spike to autonomous play.

## Attribution & license

Neural Amplifier builds on **GLSMAC** (<https://github.com/afwbkbc/glsmac>), which is licensed
under **AGPL-3.0**. GLSMAC reimplements *Sid Meier's Alpha Centauri*; it ships no copyrighted
assets and requires an existing SMAC install to play. Any C++ changes we contribute to or
distribute alongside GLSMAC inherit AGPL-3.0 obligations — a reason we keep the engine-side
footprint to a single, upstream-friendly addition.
