# Thinker Adapter Notes

The near-term engine: fork [Thinker](https://github.com/induktio/thinker) and bridge its AI
decision hooks to the orchestrator, so Claude drives a faction in the **complete, balanced**
original *Alpha Centauri*. This is the starter reference; it will grow with `file:line`
citations once we clone and read the Thinker source (not yet in this session).

> **Why this route:** the entire game already exists — production, the full tech tree, social
> engineering, diplomacy, terraforming, combat, secret projects, every faction. Unlike GLSMAC,
> there's a deep game to control on day one.

## What Thinker is

- An AI + gameplay overhaul for SMAC/SMACX. **MIT-licensed, C++.** It works by patching the
  game to load an additional **DLL**, then implementing features in C++ that read/write the
  game's in-memory structures and replace decision routines — notably **production and
  movement AI**, terraforming automation, and map/gen improvements.
- Because it's MIT, we can **adapt its code with attribution** (preserve the MIT notice on
  adapted portions). Our adapter can be a fork of Thinker or a Thinker-style companion DLL.

## The plan

1. **Clone & build the Thinker fork** (add it to the session as `scbrown/thinker` once
   forked). Capture the build toolchain (MSVC/MinGW, 32-bit) and the patcher workflow in
   `Technical.md`.
2. **Find the AI decision hooks.** Thinker already intercepts production and unit-movement AI.
   Those intercepts are our injection points: at each, we have the game state and must return
   a choice.
3. **Serialize state → contract.** At a hook, read the relevant in-memory structures (faction,
   bases, units, tiles, tech, SE) and emit the [contract](contract.md) world view. SMAC has
   **real fog-of-war**, so the world view is fair from the start.
4. **Bridge out.** POST the world view to the orchestrator and apply the returned choice at
   the hook. Use libcurl or a local socket; keep the call non-blocking / bounded so the game
   loop isn't stalled.
5. **Two tiers.** Let Thinker's existing deterministic AI handle the mechanical majority;
   route only policy-level decisions (and LLM drill-downs) through `/decide`.

## Known constraints (to design around)

- **32-bit Windows binary.** Building and running means MSVC/MinGW + Windows or **Wine/VM**.
  Automated/headless runs are awkward — SMAC is a GUI app with no clean headless mode. Plan for
  a Wine + virtual-display harness for unattended play, and expect this to be the hardest part
  of the route (worse than GLSMAC's headless story).
- **No in-isolation unit tests** of game logic — it's a closed binary. Testing is via running
  the actual game. (The orchestrator, by contrast, is fully unit-testable on fixtures — put
  logic there, keep the DLL thin.)
- **Distribution.** We can ship only the DLL + patcher; users must own SMAC and bring their own
  `terranx.exe` (Thinker's model). Don't commit or distribute game binaries/assets.
- **Fragility.** Hooking a 1999 binary via memory offsets is arcane; lean on Thinker's existing
  mapped structures rather than reverse-engineering from scratch.

## Open questions (resolve when we read the source)

- Exact hook signatures for production and movement AI, and whether other decisions (base
  siting, tech choice, SE, diplomacy) are interceptable.
- Whether Thinker exposes enough to build the full `action_space`, or we intercept a narrower
  set first.
- The cleanest non-blocking transport from inside the DLL (worker thread + queue vs. bounded
  synchronous call).
- Toolchain/CI for building the DLL reproducibly.

## Reference

- Thinker: <https://github.com/induktio/thinker> — `Technical.md` for build/compile details.
- Contract this adapter implements: [contract.md](contract.md).
