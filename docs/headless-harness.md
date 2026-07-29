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

- **`MessageBoxA` is a deadlock in disguise.** Thinker reports *fatal errors* through Win32
  modal dialogs (`exit_fail` at `src/main.cpp:429-441`; startup failures at `:451-468`). Under
  Xvfb with nobody to click OK, that is a process hung *forever*, not a failed test. The
  harness needs a **hard timeout**, and the fork should gain a flag routing fatal errors to
  stderr + non-zero exit. Small change, high value — without it this failure mode costs a
  debugging session to diagnose the first time.
  - This applies **only** to Thinker's error dialogs. The game's own in-game dialogs are a
    different thing entirely and must *not* be suppressed — see §4.
- **Run windowed.** Set `video_mode = VM_Window` (`src/main.h:179,206`, flag `-windowed`).
  Fullscreen mode-setting is handled poorly under Wine + Xvfb.
- **Never block the message pump.** Thinker installs a low-CPU idle hook (`ModPeekMessage`,
  `src/patch.cpp:135`); the orchestrator round-trip must run on a worker thread or under a
  tight bounded wait, per [thinker-adapter-notes.md](thinker-adapter-notes.md) §5.
- **Treat a hung run as a failure, not a flake.** Timeout, capture the artifacts produced so
  far, and fail — a silently-stalled game is the most likely bad outcome in this lane.

---

## 4. Dialogs, diplomacy, and custom UI

"Suppress the modal dialogs" is the wrong instinct. SMAC's dialogs are not chrome — they are
**decision points and information channels**, and several are exactly what we want Claude to
answer. Three distinct classes, which must be handled differently:

| Class | Examples | Unattended posture |
|---|---|---|
| **Thinker error dialogs** | `exit_fail`, ini/DLL startup failures (`src/main.cpp:429-441,451-468`) | **Suppress** → stderr + exit (§3.3) |
| **SMAC in-game dialogs** | `popp`, `pop_ask*`, `X_pop_*`, `NetMsg_pop` — council votes, tech choice, events, diplomacy | **Intercept**, never suppress — these are contract decision points |
| **Our own dialogs** | Copilot advice, order approval, drill-down explanation | **Add** — see §4.3 |

### 4.1 `is_human` is the master switch

Everything turns on one predicate:

```cpp
bool is_human(int faction_id) {          // src/faction.cpp:109-112
    return FactionStatus[0] & (1 << faction_id);   // FactionStatus @ 0x9A64E8
}
```

The engine branches on it in dozens of places, and it decides **whether a faction talks to a
human through dialogs or resolves decisions through AI functions**. That gives two operating
modes, and they are genuinely different products:

**Mode A — Claude drives an AI slot (`is_human == false`).** The game routes decisions to the
AI functions Thinker has already carved out: `mod_base_build` (`src/base.cpp:1145`),
`mod_enemy_move`, `mod_faction_upkeep`, `mod_tech_ai`, `mod_social_ai`, `design_units`
(`src/plan.cpp:103-104`, which early-returns on `is_human`), `council_get_vote`
(`src/engine.cpp:683`), `enemy_diplomacy`. **No dialogs fire for that faction at all.** This is
the clean unattended path for autonomous play (A2) — and note that SMAC has a *complete*
parallel AI decision path, so nothing is only reachable via a dialog.

**Mode B — Claude drives the human slot (`is_human == true`).** Every popup fires and blocks
for input. This is what **copilot mode** (VISION §7, S3) actually is, and there the dialogs are
the point.

**Mode B+** — a human slot with `conf.manage_player_bases` / `manage_player_units` enabled —
keeps fair rules *and* restores a deterministic tier, and is the recommended configuration for
autonomous play. The slot-mode decision is owned by
[thinker-adapter-notes.md](thinker-adapter-notes.md) §5.0; treat that as authoritative.

> **Fairness sting — worth resolving early.** AI slots receive difficulty handicaps and bonuses
> that human slots don't: e.g. `conf.unit_support_bonus[*DiffLevel]` applies only when
> `!is_human` (`src/base.cpp:1645`), alongside many other `is_human` branches. VISION §4 commits
> to **"no cheating"** — so putting Claude on an AI slot means it silently inherits the AI's
> advantages, and a win proves less than it appears. Either neutralise those branches for
> LLM-routed factions or record the handicap explicitly in the world view. **This is a
> correctness issue for the experiment, not a detail.**

### 4.2 Conversations between other computer players

These already happen, and Thinker already intercepts them.

- **Where AI-to-AI diplomacy is decided:** `enemy_diplomacy(faction_id)` (`0x55F930`,
  `src/engine.cpp:808`), called per faction from `mod_faction_upkeep` (`src/game.cpp:1574`).
  It is a *decision routine*, not a conversation UI — so it is interceptable exactly like the
  other AI hooks.
- **Where the outcomes surface:** the engine broadcasts them as `NetMessage` popups —
  `enemies_treaty` (three call sites) and `enemies_war` — and Thinker already redirects all of
  them through `mod_NetMsg_pop` (`src/gui.cpp:1490-1506`, hooks at `src/patch.cpp:1114-1119`)
  to implement its `foreign_treaty_popup` option, **including per-turn de-duplication**.
- **So the feed we want already exists.** Pointing `mod_NetMsg_pop` at the contract's `deltas`
  — in addition to, or instead of, a popup — gives Claude "the Hive and the University just
  signed a pact" with no new engine archaeology.
- **The mutation primitives**, for both observing and eventually injecting: `net_treaty_on` /
  `net_treaty_off` / `net_set_treaty`, `net_tech`, `net_energy`, `net_loan`, `net_maps`,
  `net_cede_base`, `net_double_cross`, `net_pact_ends` (`src/engine.h:871-884`), plus
  `propose_pact` / `propose_treaty` / `call_off_vendetta` / `buy_council_vote`.

> **Fog caveat.** `foreign_treaty_popup` surfaces *all* foreign treaty changes. A faction
> should not legitimately know about pacts between factions it has never met, so this feed must
> be gated on contact/visibility before it enters the world view — otherwise it is an
> information cheat wearing a feature's clothes.

### 4.3 Custom dialogs are already a solved, data-driven pattern

Thinker ships its own dialogs today, and the mechanism is pleasantly cheap: **a section in a
text file plus one call**. `docs/modmenu.txt` (285 lines) defines them:

```text
#OPTIONS
#xs 480
#caption Thinker Mod Options
^^Mod Version: {$MSG0}
^^Total bases: $NUM0
#itemlist
Use new random map generator.
__Set map generator emphasis on larger continents.
```

Sections are addressed by label, `#xs` sets width, `^^` lines are body text, `{$MSG0}` / `$NUM0`
interpolate strings and numbers, `__` nests list items, and `#itemlist` makes it a selectable
list. The call returns the chosen index:

| Need | Call | Example |
|---|---|---|
| Message / menu, returns choice | `popp(file, label, 0, "img.pcx", 0)` | `src/gui.cpp:1205,1221`; `src/base.cpp:1181` |
| Checkbox list | `X_pop_9(file, label, -1, 0, PopDialogCheckbox\|PopDialogBtnCancel, 0)` | `src/gui.cpp:1140` |
| Numeric input | `pop_ask_number_4(file, label, value, 0)` | `src/gui.cpp:706,759` |
| **Override a stock game dialog** | `mod_BasePop_start` + a `movedlabels` set redirects a stock label to your own file | `src/gui.cpp:1507-1513` |
| Real Win32 menu entries | `pfncMainMenuAddSubMenu` / `AddBaseMenu` / `AddSeparator` | `src/gui.cpp:131-135` |

So a **Neural Amplifier advisory dialog** — copilot mode showing Claude's proposed orders and
reasoning for approval — is a `na.txt` section plus a `popp` call, with `{$MSG0}` carrying the
model's reasoning text. The `movedlabels` redirect in `mod_BasePop_start` is the pattern for
*replacing* an existing game dialog (e.g. annotating the production picker) without editing the
original game files.

The full inventory of which decisions have an AI path at all — and which are dialog-only — is in
**[game-surface.md](game-surface.md)**.

This makes copilot mode (S3) far cheaper than it looks — and it is the same seam that lets us
render *why* Claude chose something, which is the project's whole legibility pitch.

---

## 5. Why this lane pays for itself: fixture harvesting

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

## 6. Proposed sequencing

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

## 7. Open questions

1. ~~**Which slot does Claude occupy?**~~ **Resolved** — see
   [thinker-adapter-notes.md](thinker-adapter-notes.md) §5.0. A third option exists: **Mode B+**
   (human slot with `conf.manage_player_bases` / `manage_player_units`) gives fair rules *and* a
   deterministic tier. Recommendation is B+ for autonomous, B for copilot, A for plumbing spikes
   only. What remains open is §4.1's fairness sting itself — whether to neutralise the handicap
   branches or record them — tracked in
   [game-surface.md](game-surface.md) §7.3.
2. **The other human slot.** A normal game still has a human faction. If Claude runs an AI slot
   unattended, does the remaining human slot still raise blocking popups — and what occupies
   it? Investigate `auto_play_callback` (`0x50E890`, `src/engine.cpp:638`), which looks like
   SMAC's own autoplay hook and may be the intended unattended driver.
3. **Fog-gating the foreign-diplomacy feed.** `mod_NetMsg_pop` sees all foreign treaty changes;
   the world view must filter to what the faction has legitimately contacted (§4.2).
4. **Minimum file set.** How much of the install does a headless run actually need? A trimmed
   fixture is faster to mount and easier to verify. Determine empirically at step 3.
5. **Steam binary version.** Expected v2.0, unconfirmed until the checksum is run against a
   real install. If it differs, the ISO + official v2.0 patch becomes the primary path.
6. **Canned-save provenance.** Generated by hand once and committed (saves are small and are
   our own game state, not game assets), or regenerated by script? Affects how reproducible
   the harness is for a new contributor.
7. **Turn-count exit.** Does `-na-exit-turn` cleanly unwind the game loop, or is a harder
   process kill after the final autosave the pragmatic answer?
8. **Wine determinism.** How reproducible is a run across Wine versions? Pin the Wine version
   in the harness image regardless.
9. **Where the harness image lives.** A committed `Dockerfile` (Wine + Xvfb + MinGW, no game)
   that mounts `$SMAC_DIR` at run time keeps the assets out of the image entirely.
