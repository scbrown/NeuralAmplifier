# Neural Amplifier

An LLM brain for *Sid Meier's Alpha Centauri*. Each turn an engine adapter hands Claude a
fog-limited **world view** plus a menu of **legal actions**; Claude reasons and returns orders;
the engine validates and executes them. One platform-agnostic brain, two engine adapters, one
JSON contract.

This is the design documentation. The project's front door is the
[README](https://github.com/scbrown/NeuralAmplifier#readme); the long-form argument and roadmap
are in [VISION.md](https://github.com/scbrown/NeuralAmplifier/blob/main/VISION.md).

## Where to start

Read [the contract](contract.md) first whatever you are here for — it is the interface every
other document is describing one side of.

| If you're working on… | Read first |
| --- | --- |
| Anything at all | [The Contract](contract.md) |
| A Thinker hook, or which faction slot Claude drives | [Thinker Adapter Notes](thinker-adapter-notes.md) |
| Running the game unattended, dialogs, the game fixture | [Headless Harness](headless-harness.md) |
| What an AI player must cover, or adding a decision hook | [The Game Surface](game-surface.md) |
| Moving a surface to the LLM tier, or a poor decision | [Decision Inputs](decision-inputs.md) |
| How one decision steers a later one | [Directives](directives.md) |
| Tests, CI lanes, or fixtures | [Building & Testing](building-and-testing.md) |
| Logging, metrics, tracing, coverage | [Observability](observability.md) |
| A GLSMAC mod or the GSE builtin | [GLSMAC Integration Notes](glsmac-integration-notes.md) |
| Knowledge, memory, or guardrails | [Knowledge & Guardrails](knowledge-architecture.md) |

## The shape of it

The brain is platform-agnostic and speaks only the contract; engine specifics stay in the
adapter. Everything Claude is told comes from the engine's own world view, annotated with
retrieved facts — and everything it decides is checked back against the action space the engine
offered, because **the engine is authoritative** and an illegal order must be impossible rather
than merely unlikely.

Two sibling services back the brain, both optional and neither on the critical path: **Quipu**
holds governed, persisted knowledge, and **Hank** serves policy guardrails. A knowledge layer
that is down means a less-informed decision, never a stalled turn.
