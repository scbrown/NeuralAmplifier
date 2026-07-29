# Headless Harness

How we get a real *Alpha Centauri* under automation — the **game fixture** (making an owned
copy of SMAC available to the test harness) and the **unattended run** (driving it with no
human at the keyboard).

Companion to [building-and-testing.md](building-and-testing.md), which sets the strategy:
*push testing down to where it's fast, free, and deterministic.* This doc covers the one lane
that can't be pushed down — a real game — and how to make it cheap enough to be worth running.

Engine specifics are grounded in source: `thinker/...` paths cite the fork at
[`scbrown/thinker`](https://github.com/scbrown/thinker); GLSMAC claims are carried from
[glsmac-integration-notes.md](glsmac-integration-notes.md).

---

## 1. Two different "headless"

Conflating these is the most expensive mistake available here, so name them separately:

| | **Thinker / `terranx.exe`** | **GLSMAC `--gse-tests`** |
|---|---|---|
| What it means | Unattended — no *visible* display, no human input | Truly headless — `graphics::Null`, no display at all |
| How | Wine + **Xvfb** (virtual framebuffer); the game still renders | Null subsystems; nothing renders |
| Needs game assets? | Yes — a real SMAC install | **No** |
| CI-able? | No (assets + Wine) — local / nightly | Yes |
| Is there a game to play? | **Yes — the complete, balanced game** | No — production, tech, diplomacy, fog don't exist yet |

`terranx.exe` is a 32-bit Windows GUI binary with a render loop. **It cannot run truly
headless.** Wine + Xvfb is the achievable target: unattended, containerizable, no visible
display. That is sufficient — "no human present" is the property we actually need.

The genuinely headless path (`--gse-tests`) is real but currently has no game behind it, so it
serves as a **CI canary for mod logic**, not as a source of gameplay feedback. Both matter;
neither substitutes for the other.

---

## 2. The game fixture

### 2.1 What decides compatibility

One checksum. Thinker requires **Alien Crossfire v2.0 `terranx.exe`** — 3 084 288 bytes,
SHA-1 `4b19c1fe3266b5ebc4305cd182ed6e864e3a1c4a` (`thinker/Technical.md:193-195`). The same
passage notes that *"using other official game binaries of the same version should also
work"* — so **GOG is not special; v2.0 is**. Game version 1.0 is explicitly unsupported.

This is not a soft requirement. Both hook primitives verify the original bytes before
patching and abort on mismatch (`thinker/src/patch.cpp:218,250`), so an incompatible binary
fails loudly at startup rather than corrupting a run.

The verification is therefore trivial:

```bash
sha1sum terranx.exe   # want: 4b19c1fe3266b5ebc4305cd182ed6e864e3a1c4a
```

### 2.2 Sourcing: Steam primary, ISO as validator

| Source | Role | Notes |
|---|---|---|
| **Steam** — [Planetary Pack](https://store.steampowered.com/app/2204130/Sid_Meiers_Alpha_Centauri_Planetary_Pack/) (app 2204130) | **Primary** | Ships SMAC + SMACX; community reports Thinker and PRACX working against it. Expected to be v2.0 already — confirm with the checksum. |
| **Physical media (ISO backup)** | **Validator** | Discs are v1.0, which Thinker does not support; requires the official Alien Crossfire v2.0 patch. Slower to prepare. |

The ISO's job is **not** to be a spare copy. It is to prove the fixture is reconstructible
from media we physically own, independent of a storefront account. If an ISO-derived install
reproduces the same manifest as the Steam-derived one, the fixture's provenance is settled and
the harness is no longer coupled to Steam.

Steam is only ever a *source*, never a runtime dependency: Thinker never modifies
`terranx.exe` on disk — it applies every patch in memory at DLL attach
(`thinker/Technical.md:198-200`, `src/main.cpp:446-466`). So an extracted directory tree runs
on its own, with no Steam client in the loop. That is what makes containerizing it viable.

### 2.3 Shape of the fixture

The game directory is extracted **once** and referenced by env var, mirroring the existing
`GLSMAC_DIR` convention (`justfile:8`):

```text
$SMAC_DIR/            # extracted once, lives outside the repo, never committed
  terranx.exe         # Alien Crossfire v2.0 — the checksummed anchor
  alphax.txt          # rules/data — also the Quipu datalinks source (K1)
  ...                 # PCX / TTF / WAV assets
```

**The repo holds the manifest, never the bytes.** Game data is copyrighted; it cannot go in
the repository or into public CI. What is version-controlled is a manifest of relative paths
plus checksums, and a `just game verify` recipe that resolves `$SMAC_DIR`, checks the
manifest, and reports precisely what is missing or wrong. The `check-added-large-files`
pre-commit hook is a useful second line of defence against an accidental commit.

**One fixture serves both engines.** A full GLSMAC game also requires a real SMAC install —
`ResourceManager` probes for marker files and genuinely decodes `texture.pcx` / `ter1.pcx`,
with no synthetic fallback ([glsmac-integration-notes.md](glsmac-integration-notes.md) §5).
So the fixture is engine-independent and worth building properly once.

---

## 3. Running unattended

### 3.1 The real blocker is menus, not rendering

Xvfb solves the display. It does not solve the fact that a fresh `terranx.exe` boots to a main
menu and waits for clicks — there is no human in CI to start a game, pick a faction, and
choose a map.

**Thinker already exposes the seams to skip this entirely.** Three, all pre-existing:

| Seam | Where | What it buys |
|---|---|---|
| `load_daemon` / `save_daemon` | `src/engine.cpp:1049,1051` (`0x5A9760` / `0x5A94F0`); also `load_game` at `:1056` | Load a prepared savegame **programmatically** — no menu navigation. `mod_load_daemon` (`src/game.cpp:1182`, hooked at `src/patch.cpp:635`) is existing precedent for driving loads from mod code. |
| `cmd_parse` | `src/main.cpp:403-423` | Already parses custom flags (`-smac`, `-native`, `-screen`, `-windowed`) into `Config`. The launcher forwards argv straight through to the game (`src/launch.cpp:65-71`), so **new flags need no launcher change**. |
| `mod_auto_save` | `src/game.cpp:1188-1197`; `autosave_interval` defaults to `1` (`src/main.h:223`) | Already writes `saves/auto/Autosave_<year>.sav` **every turn** — a per-turn artifact stream for assertions and replay, for free. |

Adding `-na-autoload <save>`, `-na-endpoint <url>`, and `-na-exit-turn <n>` is a handful of
lines inside a function that already exists, plus the matching `Config` fields — the same
pattern §6 of [thinker-adapter-notes.md](thinker-adapter-notes.md) describes for
`llm_factions` / `llm_endpoint`.

### 3.2 The run recipe

```text
canned save + thinker.ini + -na-* flags
        │
        ▼
  Wine + Xvfb (windowed)
        │
        ▼
  DLL auto-loads the save at startup      ← no menus
        │
        ▼
  decision hooks fire, N turns elapse
        │
        ▼
  clean exit  →  artifacts: per-turn autosaves + JSONL decision log
```

Determinism comes from the canned save plus a fixed turn count: the same save and the same
scripted decisions should produce the same autosave sequence.

### 3.3 Hazards to design around now

- **`MessageBoxA` is a deadlock in disguise.** Thinker reports fatal errors through modal
  dialogs (`exit_fail` at `src/main.cpp:429-441`; startup failures at `:451-468`). Under Xvfb
  with nobody to click OK, that is a
  process hung *forever*, not a failed test. The harness needs a **hard timeout**, and the
  fork should gain a flag routing fatal errors to stderr + non-zero exit. Small change, high
  value — without it this failure mode costs a debugging session to diagnose the first time.
- **Run windowed.** Set `video_mode = VM_Window` (`src/main.h:179,206`, flag `-windowed`).
  Fullscreen mode-setting is handled poorly under Wine + Xvfb.
- **Never block the message pump.** Thinker installs a low-CPU idle hook (`ModPeekMessage`,
  `src/patch.cpp:135`); the orchestrator round-trip must run on a worker thread or under a
  tight bounded wait, per [thinker-adapter-notes.md](thinker-adapter-notes.md) §5.
- **Treat a hung run as a failure, not a flake.** Timeout, capture the artifacts produced so
  far, and fail — a silently-stalled game is the most likely bad outcome in this lane.

---

## 4. Why this lane pays for itself: fixture harvesting

A slow game run whose only output is "it worked" is not worth its cost. **The output that
matters is recorded world-view JSON.**

Every unattended run should dump the [contract](contract.md) world view at each decision point.
Those recordings become orchestrator fixtures *and* contract-test fixtures, which means the
overwhelming majority of iteration happens afterwards in the free, fast Python lane with **no
game running at all**.

[building-and-testing.md](building-and-testing.md) §5 already names this for GLSMAC —
"recording a real world view produces an orchestrator fixture *and* a contract fixture from one
run". The same principle is what makes the Thinker lane survivable, and it has to be designed
in from the first spike rather than retrofitted.

The resulting cadence:

- **Rarely** — run the game to *harvest* fixtures and catch integration drift.
- **Constantly** — iterate against those fixtures, in CI, for free.

---

## 5. Proposed sequencing

Ordered so each step is verifiable **without the next one existing**, and the slow lane is
built last. Maps onto [VISION.md](../VISION.md) §7.

| Step | What | Game needed? | Roadmap |
|---|---|---|:---:|
| **0** | Game fixture: manifest, `just game verify`, `$SMAC_DIR` wiring, Steam + ISO extraction docs | own it, don't run it | — |
| **1** | Orchestrator walking skeleton — `POST /decide` returns a choice from `action_space`; fake Claude; fixtures | ❌ | S1 |
| **2** | Cross-compile `thinker.dll` in CI — `apt install build-essential cmake g++-mingw-w64-i686-posix` (`thinker/Technical.md:157`), `cmake --preset develop` | ❌ | A0 (build half) |
| **3** | Headless harness: Wine + Xvfb + canned save + `-na-*` flags; DLL logs one decision and dumps its world view | ✅ local / nightly | A0 |
| **4** | Close the loop: DLL POSTs the world view, applies Claude's returned choice | ✅ local / nightly | A1 |

Steps 1 and 2 together prove a complete **dev → test → release** cycle with no game
whatsoever: tests run in existing CI (`.github/workflows/ci.yml`), and `thinker.dll` is a
genuine release artifact built on every push. That is the cheapest available proof of the
cycle, and it de-risks step 3 by removing the toolchain from the list of unknowns.

**First decision hook: `mod_base_build`** (`thinker/src/base.cpp:1145`). One `int` in, one
`int` out, self-contained — already identified as the best first spike in
[thinker-adapter-notes.md](thinker-adapter-notes.md) §5.

---

## 6. Open questions

1. **Minimum file set.** How much of the install does a headless run actually need? A trimmed
   fixture is faster to mount and easier to verify. Determine empirically at step 3.
2. **Steam binary version.** Expected v2.0, unconfirmed until the checksum is run against a
   real install. If it differs, the ISO + official v2.0 patch becomes the primary path.
3. **Canned-save provenance.** Generated by hand once and committed (saves are small and are
   our own game state, not game assets), or regenerated by script? Affects how reproducible
   the harness is for a new contributor.
4. **Turn-count exit.** Does `-na-exit-turn` cleanly unwind the game loop, or is a harder
   process kill after the final autosave the pragmatic answer?
5. **Wine determinism.** How reproducible is a run across Wine versions? Pin the Wine version
   in the harness image regardless.
6. **Where the harness image lives.** A committed `Dockerfile` (Wine + Xvfb + MinGW, no game)
   that mounts `$SMAC_DIR` at run time keeps the assets out of the image entirely.
