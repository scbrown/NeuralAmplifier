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

Not a checksum — a **build**. Thinker requires **Alien Crossfire v2.0 `terranx.exe`**. Game
version 1.0 is explicitly unsupported. `thinker/Technical.md:193-195` cites one v2.0 binary,
3 084 288 bytes, SHA-1 `4b19c1fe3266b5ebc4305cd182ed6e864e3a1c4a`, but the same passage allows
*"using other official game binaries of the same version should also work"* — so **GOG is not
special; v2.0 is**, and that published hash is one known-good value, not the gate.

The gate is the **patch sites**. Both hook primitives verify the original bytes before patching
and abort on mismatch (`thinker/src/patch.cpp:218,250`), so an incompatible binary fails loudly
at startup rather than corrupting a run. A binary whose hash is unlisted but whose patch sites
match is fine, and the game itself tells you which you have.

**Measured, 2026-07-29** — the Steam Planetary Pack binary is one of those unlisted-but-valid
builds:

| | Bytes | SHA-1 |
|---|---|---|
| Technical.md anchor (GOG) | 3 084 288 | `4b19c1fe3266b5ebc4305cd182ed6e864e3a1c4a` |
| **Steam, app 2204130** | **3 094 576** | **`7bbcc54e64760c11a24f48862f15dbaaeab61435`** |

10 288 bytes larger and a different hash — yet Thinker v5.4 attaches and plays it. Its PE link
timestamp is 1999-12-20, i.e. an original Firaxis build rather than a storefront repack. So:

```bash
sha1sum terranx.exe   # 4b19c1fe… (GOG) and 7bbcc54e… (Steam) are both known-good v2.0
```

**Never hard-fail the fixture on a hash mismatch.** An unknown hash is a prompt to record a new
provenance line and let Thinker adjudicate, not grounds to reject the install. The authority is
`patch.cpp`, and it reports at startup.

### 2.2 Sourcing: Steam primary, ISO as validator

| Source | Role | Notes |
|---|---|---|
| **Steam** — [Planetary Pack](https://store.steampowered.com/app/2204130/Sid_Meiers_Alpha_Centauri_Planetary_Pack/) (app 2204130) | **Primary — confirmed working** | Ships SMAC + SMACX at v2.0. Verified 2026-07-29: Thinker v5.4 patches and plays it (§2.1). No patching step needed. |
| **Physical media (ISO backup)** | **Optional validator** | Discs are v1.0, which Thinker does not support; requires the official Alien Crossfire v2.0 patch. Slower to prepare, and **no longer on the critical path**. |

The ISO's job is **not** to be a spare copy, and it is not a prerequisite — Steam alone yields a
playable fixture. Its only remaining job is to prove the fixture is reconstructible from media we
physically own, independent of a storefront account. If an ISO-derived install reproduces the
same manifest as the Steam-derived one, the fixture's provenance is settled and the harness is no
longer coupled to Steam. Worth doing eventually; nothing blocks on it.

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
the repository or into public CI. What is version-controlled lives in `fixtures/smac/`:

| File | Role |
|---|---|
| `steam-2204130.manifest` | `sha1 <TAB> size <TAB> relpath`, sorted — the reference fixture (1 624 files) |
| `overlays.tsv` | Known **mod** hashes, so a mismatch can be *explained* rather than merely reported |
| `PROVENANCE.md` | Where the bytes came from, which `terranx.exe` hashes we accept, what is unresolved |

Driven by `scripts/game_fixture.py` through `just game`:

```bash
just game verify    # check $SMAC_DIR against the manifest
just game strict    # also fail on files present but absent from the manifest
just game scan      # regenerate the manifest from $SMAC_DIR
```

The `check-added-large-files` pre-commit hook is a useful second line of defence against an
accidental commit of the bytes themselves.

**One fixture serves both engines.** A full GLSMAC game also requires a real SMAC install —
`ResourceManager` probes for marker files and genuinely decodes `texture.pcx` / `ter1.pcx`,
with no synthetic fallback ([glsmac-integration-notes.md](glsmac-integration-notes.md) §5).
So the fixture is engine-independent and worth building properly once.

### 2.4 Keep the fixture separate from the directory you play in

The failure this prevents is quiet and expensive, so it gets its own section.

**A mod installs *into* the game directory.** Thinker extracts over the top of an existing
install and overwrites data files — measured on the gaming host, it replaces `alphax.txt`,
`german/alphax.txt`, and 15 `basenames/*.txt` with its own copies, byte-identical to
`Thinker_v5.4.zip`. Nothing warns you. The directory still looks like a game directory.

**`alphax.txt` is the one that hurts.** `just ingest` reads `$SMAC_DIR/alphax.txt` and labels
the result **canonical**. Thinker ships its own tech tree and rules, so pointing `$SMAC_DIR` at
a Thinker-modded directory silently launders house-rule data into the canonical `smac:` graph —
precisely what invariant 4 forbids and what `just ingest-thinker` (tier `house-rule`) exists to
prevent. A checksum manifest catches this; a directory listing does not.

So:

- `$SMAC_DIR` is a **pristine** game tree — the provenance anchor and the canonical K1 source.
- The **play** directory is a separate copy with the mod overlaid on it.
- `just game scan` **refuses** a tree whose files match a known overlay hash, rather than baking
  mod hashes into the canonical manifest. Override with `--allow-contaminated` and it records
  the pristine files only, listing the rest as `unresolved` in the manifest header.

To restore a contaminated tree: Steam → Properties → Installed Files → **Verify integrity of
game files**. It re-downloads tracked files and leaves untracked additions (`thinker.dll`,
`thinker.exe`, `smac_mod/`) in place. Then re-scan.

---

## 3. Running unattended

### 3.0 Host setup and the one-command launch

**Verified working, 2026-07-29.** Our Thinker fork, cross-compiled on Linux, drives the Steam
Alien Crossfire `terranx.exe` under Wine 11.9 + Xvfb with no visible display and no Steam client
in the loop.

Dependencies, all installed by `just setup-host` (`scripts/setup-host.sh`):

| Dependency | Why |
|---|---|
| `build-essential`, `g++-mingw-w64-i686-posix` | Thinker is a 32-bit Windows DLL; Linux cross-compile is upstream-supported (`thinker/Technical.md`). GCC < 8.1.0 unsupported. |
| `cmake` ≥ 3.31 | Required by the fork's `CMakePresets.json`, newer than several distro packages |
| `wine` | `terranx.exe` is a 32-bit Windows GUI binary |
| `xvfb` | The virtual display. The game still renders — nobody is watching (§1) |

Then one command builds the fork, installs it over a real install, and launches:

```bash
just thinker-play             # on your current display
just thinker-play headless    # on a virtual display (Xvfb)
just thinker-play build       # build + install, don't launch
just thinker-play restore     # put stock Thinker back
```

Three things `scripts/play-thinker.sh` is careful about, each a bug we actually hit:

- **It creates its own Wine prefix** (`~/.local/share/na-wine`) and never touches Steam's Proton
  prefix. A prefix upgraded by a different Wine version is not reversible, so the game you launch
  from Steam and the game the harness drives must stay independent.
- **It backs up stock Thinker exactly once**, and refuses to record one of our own builds as the
  restore point — otherwise a hand-installed DLL gets frozen in and `restore` silently restores
  our build forever.
- **It verifies the artifact is `PE32 … i386`** before installing. The fork's `CMakeLists.txt`
  sets `CMAKE_CXX_COMPILER` *after* `project()`, which is fragile enough to be worth asserting.

On prefix architecture: `setup-environment.sh` notes that a `WINEARCH=win32` prefix is required.
That holds for older Wine; **Wine 9+ runs 32-bit binaries in a default 64-bit prefix via
new-wow64**, and SMAC was verified booting that way here on Wine 11.9.

Two display settings that are not optional in practice, both measured on a 4K host:

| Symptom | Cause | Fix |
|---|---|---|
| UI renders at a quarter scale, unreadable | `video_mode=0` is fullscreen at the *native* desktop resolution, and SMAC's UI is fixed-size | `video_mode=2` (borderless windowed) with `window_width=2560`, `window_height=1440` |
| Clicks land far from the cursor | Wine maps pointer coordinates against the full desktop while the window is at the game's resolution | A Wine **virtual desktop** at exactly the game's resolution, so Wine owns window and coordinate space together |

`play-thinker.sh` sets the virtual desktop automatically from `window_width`/`window_height`
(disable with `NA_VIRTUAL_DESKTOP=`) and warns about the resolution combination. Windowed mode is
also simply the better way to work: the decision log stays visible next to the game.

The gate is off unless configured. `llm_factions=0` (the default) behaves exactly like stock
Thinker, so an unconfigured build produces no observations rather than an error.

### 3.0.1 Loading a savegame unattended — the working sequence

**Solved 2026-07-29.** `-na-autoload <path>` reaches a playable session with no human
input: correct turn, correct faction, map rendered, no dialog on screen.

The insight that mattered is a negative one. Every attempt to *construct* the
menu-to-session transition failed (§3.0.2 lists six). So don't construct it — let the
engine perform it, then swap the state:

```text
1. click QUICK START      starts a game. Critically it opens NO file picker, and
                          modal dialogs freeze every thread we have (§3.0.2), so
                          "never open a modal" is a hard constraint.
2. press Enter            confirms the PLANETFALL intro dialog. A keystroke rather
                          than a click: no coordinates, so a resolution change
                          cannot break it.
3. load the savegame      the engine's own replay/undo sequence, which works here
                          precisely because we are now already in a game with the
                          display up.
```

Step 3 is the engine's own code, copied from the replay path at `0x5ADCD0`:

```c
mod_load_daemon(path, 0)   // flag 0, not 1
call 0x5FD120              // cdecl, no arguments
GameHalted = 0
```

Implemented as a state machine in the fork's `neural.cpp`. It waits on observable
conditions where they exist — `GameHalted` clearing is the real signal a session is live —
and gives up after 40s rather than looping. The startup wait is 12s because firing earlier
produced a *flawless log and a main menu*: the engine's own startup drew over the session
we had just entered, which is indistinguishable from having done nothing.

QUICK START needs coordinates, so they are stored as **fractions of the client area** and
resolved through `GetClientRect`, not as pixels.

The wasted map QUICK START generates is discarded a second later. Irrelevant for a
harness.

### 3.0.2 How menu and dialog interaction actually works

Measured on the gaming host, 2026-07-29, mostly by getting it wrong several times. This
section exists so the next attempt starts from evidence instead of repeating the same
four dead ends.

**There are three distinct UI layers, and they behave completely differently.**

| Layer | Pumped by | Can we drive it? |
|---|---|---|
| **Startup screen** — START GAME / QUICK START / LOAD GAME | the normal window loop, so `ModWinProc` runs | **Yes.** An in-process `PostMessage` click opens LOAD GAME |
| **Modal dialogs** — the file picker, and anything drawn over the game | its own nested message pump | **No.** See below |
| **In-game menu bar** — the `Menu` class at `0x5FB100` | normal loop | Yes, but it is not involved in startup |

Conflating these wasted most of an evening. The startup screen was never the problem.

**External input does not reach the game at all.** `terranx.exe` reads the mouse through
DirectInput while running inside a Wine virtual desktop, so the X coordinate space is not
the one it believes in. Tried and verified failing: window-message injection via
`xdotool --window`, and XTEST with warped absolute coordinates. Both report success and
change nothing. In-process `PostMessage` works, so all input must originate inside the
DLL.

**Modal dialogs freeze *everything* of ours, including a worker thread.** This is the
finding that matters, and it is not obvious. The command channel is polled from
`ModWinProc`, which the engine stops calling during a modal's nested pump — expected. So
input was moved onto a dedicated thread, on the reasoning that `PostMessage` is
thread-safe and a thread is independent of the message pump. **It is not.** A heartbeat
counter proved it: the thread ticked normally at the startup screen, then froze at the
exact instant the picker opened, and never resumed, while the process stayed alive.

Two hypotheses were tested and eliminated along the way, both plausible and both wrong:

- *The picker changes the working directory, so relative channel paths break.* No — the
  process CWD was unchanged throughout.
- *Thinker replaces the CRT's file locking with one global mutex (`patch.cpp:14-20`,
  `1148-1159`) and our DLL shares `msvcrt` with the game, so `fopen` from the thread
  deadlocks against the picker's directory I/O.* Genuinely plausible, and rewriting the
  thread to use `CreateFile`/`ReadFile`/`DeleteFile` instead of stdio changed nothing.

So the conclusion is architectural rather than a bug to fix: **while a modal dialog is
open, no code of ours runs.** Anything requiring interaction inside a modal dialog is
unreachable, and the only viable strategy is to never open one.

**`0x68F21C` is a state flag, not a trigger.** Thinker calls it `GameHalted`; PRACX names
the same address `pfGameNotStarted` (`gui.cpp`). Clearing it *asserts* that a game is
running — it does not start one. Every early attempt to enter a session by writing this
address failed for that reason, and the failure looks like success in a log.

**What loading a savegame actually requires.** The engine performs a complete
load-and-resume on itself in the replay/undo path at `0x5ADCD0`:

```c
mod_load_daemon(path, 0)   // flag 0, not 1
call 0x5FD120              // cdecl, no arguments
GameHalted = 0
```

Replicated exactly, that reaches live state — correct turn, correct faction, nothing
modal. But the **display stays on the startup screen**, because that path runs while
already in a game and so never needs window setup. Attempting to bolt on the init calls
from `0x58F450`'s tail (`0x50F440`, `0x6169D0`, `0x616950` with `ecx=0x9B90D8`) changed
nothing.

**Dead ends, recorded so they are not retried:**

| Attempt | Result |
|---|---|
| Write `GameHalted = 0` after loading | State loads, stays on menu |
| `0x58F450(1, arg2)`, `arg2 ∈ {0, 1}` | Loads, but raises the engine's own picker over the session |
| `0x58F450(1, arg2)`, `arg2 ∈ {2, −1}` | No modal, but ends back at the startup screen |
| Init calls from `0x58F450`'s tail | No effect |
| Worker thread for input during modals | Thread freezes with the modal |
| Hooking `mod_blink_timer` for startup work | Only patched in when `smooth_scrolling=1`; absent by default, so it silently never runs |

### 3.0.3 The command channel — driving and inspecting the game in-process

External control of this game does not work at all (§3.0.2), so the fork carries its own
channel. Two files in the game directory, two owners, deliberately separate so there is no
race over who consumes a command:

| File | Owner | Commands |
|---|---|---|
| `na-command` | the window procedure | `shot [path]`, `load <path>`, `observe <base_id>`, `enter <a> <b>` |
| `na-input` | a worker thread | `click x y`, `dclick x y`, `key <vk>`, `text <s>` |

Results are written to `na-command-result` / `na-input-result` as one JSON object carrying
`turn` and `halted`, so a caller can tell *not processed yet* from *failed*.

Notes that matter:

- **`shot` is an in-process `BitBlt` to a 24-bit BMP**, not an X screenshot. It needs no
  compositor, no portal permission and no X server, so it works identically under Xvfb.
  Convert to PNG outside the game.
- **`observe <base_id>` is the test loop.** It emits one `base.production` observation by
  calling only the serialiser — never `mod_base_build` — so it has no effect on production,
  minerals or the governor. It exists because in-game input cannot be driven at all, so
  ending a turn on demand is impossible and waiting for natural upkeep is not a usable loop.
- **The worker thread must not use stdio.** Thinker replaces the CRT's file locking with a
  single global mutex (`patch.cpp:14-20`, `1148-1159`) and the DLL shares `msvcrt` with the
  game, so the thread uses `CreateFile`/`ReadFile`/`DeleteFile` instead. (This was *not*
  what caused the modal freeze — see §3.0.2 — but it is still the correct thing to do from
  a second thread.)
- **The channel dies inside modal dialogs**, thread included. Design around it.

**Testing without a human or a screenshot.** After an autoload attempt, write `shot`
to the command channel and read `na-command-result`:

- no result file → a modal is blocking, or nothing of ours is running
- `turn > 0` → savegame state loaded
- `halted == 0` → the session is asserted

The one thing this cannot see is *live state behind a still-rendered menu*, which was the
actual failure for several iterations — so confirming the display genuinely needs a
capture (`just game-screen shot`) or a window-visibility flag we have not identified.

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
>
> **Gated in the orchestrator too.** The adapter should filter at source, but a thin DLL and
> a good intention are not a control, so `fog.redact()` drops any delta naming a faction
> outside the world view's `contacts` list **before the brain call** — a redaction applied
> after the prompt would be theatre. Deltas naming no parties are public news and survive.
> Removals are counted on the decision record (`redacted_deltas`), and a world view that
> omits `contacts` sets `fog_enforced: false` rather than being reported as clean: we cannot
> tell a legitimate delta from a leaked one, and absence of evidence gets recorded as such.

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
   unattended, does the remaining human slot still raise blocking popups — and what occupies it?

   **`auto_play_callback` is not the answer.** It has **zero call sites** in the fork
   (`engine.cpp:638` declares the pointer and nothing invokes it), and its address `0x50E890`
   sits inside a contiguous block of `void(int)` **GUI timer callbacks** — `mandate_color`
   (`0x50E820`), then `blink_timer` (`0x50EA40`), `blink2_timer`, `line_timer`, `turn_timer`
   (whose `int` is annotated *unused*), `go_timer`. Its siblings are patched at GUI offsets and
   reimplemented in `gui.cpp` (`mod_blink_timer`, `mod_turn_timer` at `patch.cpp:445,1158`), so
   this is SMAC's attract-mode/animation tick, not an unattended AI driver. The name is
   misleading. Drive the run from `load_daemon` + `cmd_parse` + `mod_auto_save` (§3) instead.
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
