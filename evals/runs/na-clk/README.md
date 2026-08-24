# na-clk seed 1

Two rows, on sattler's ruling: the refusal below was of the RUN, not of the SEED. Seed 1 itself
clears viability once thinker `be1b51b` is in, so rerunning it is the sequential discipline rather
than a substitution — and the first attempt stays in the record with its cause.

| arm | outcome |
|---|---|
| `brain-pre-bg4fix` | refused as unplayable at turn 123 — the run below |
| `brain` | **unresolved at turn 126 — the engine crashed** (na-2dg) |

## Row 1 proper, under `be1b51b`

Not a loss and not a result: `unresolved`. The engine crashed at turn 126 with `c0000005` at
`004BF423` — **byte-identical registers to the crash that ended the first attempt at turn 122**,
on a different build 2.5 hours earlier. Both fired on dismissing the dialog that reports our HQ
base being lost. Filed as na-2dg; two identical register dumps on two builds is a deterministic
path, not memory corruption that happened to land twice.

What the run did measure, and it is the useful part:

| turn | 1 | 2 | 3 | 4 | 5 | 6 | **7 (ours)** |
|---|---|---|---|---|---|---|---|
| 20 | — | — | — | — | — | — | **viability ok — the bar cleared for the first time** |
| 82 | 21 | 22 | 25 | 17 | 11 | 31 | **2** |
| 126 | — | — | — | — | — | — | **HQ captured by University** |

Level with the AIs at turn 20 and on 2 bases against 11-31 by turn 82. **The collapse is after
turn 21, not a bad start** — which is what makes the turn-20 checkpoint necessary and not
sufficient, and what sent the investigation to the movement path rather than the map.

Cause traced in na-1lj: colony pods spend their FULL movement allowance every turn and do not
change tile (`spent=3 speed=3`, position unchanged across three turns, waypoint stable eleven
tiles away). Zero displacement on a fully spent budget is a move attempted and failing, not a
slow unit.

## The refused attempt

**Refused as unplayable at turn 123. It measured a faction that could not expand, not the quality
of its decisions.**

    arm brain, seed 1, faction 7 (Peacekeepers), human slot, talent difficulty
    bases at turn 123:  1:32  2:51  3:36  4:43  5:22  6:49  7:0
    126 decisions, 0 degraded, USD 0.54 of the ~USD 31 approved
    raw record: ~/workspace/na-runs/lad1-crash/

## What happened

Faction 7 founded **one base and never a second**. Censused by replaying this run's own autosaves,
so this is measured at three points and not inferred from one reading:

| turn | 1 | 2 | 3 | 4 | 5 | 6 | **7 (ours)** |
|---|---|---|---|---|---|---|---|
| 13 | 2 | 2 | 2 | 2 | 2 | 2 | **1** |
| 21 | 2 | 3 | 3 | 3 | 2 | 3 | **1** |
| 123 | 32 | 51 | 36 | 43 | 22 | 49 | **0** |

Every worldview from turn 1 to 122 decides about `base_id 0` and no other. That base grew to
population 6, starved down 6 -> 5 -> 4 -> 3 over turns 119-122, and was gone by turn 123.

**This is not a map fault.** The start was normal and the base grew in place; seed 4242's
open-ocean start (na-ywn) is what a map fault looks like and this is not it.

**Nor is it a settle failure of the kind na-ywn closed on.** That fix (`b36bc52`) is working —
without it this seed had zero bases, and it produced the first one. What it did not produce is
the second: by turn 13 every AI faction has two and ours still has one, and it is still one 100
turns later. So the open question is the *next* pod, not the first, and na-ywn's close reason
("7:2 at turn 6 and turn 13 after") does not reproduce on this seed.

A faction on one base cannot beat six CPU factions on 200+, however well it decides. **So na-xb1's
goal cannot be measured until the LLM faction can expand**, and no number of seeds changes that.

The turn-21 row is the one that matters for the harness: our faction is below the bar of two bases
and every AI is above it, which is exactly the asymmetry the viability check was built to catch —
and it would have been caught at turn 20 rather than turn 123 had anything been running it.

## Two harness defects this run found, both now fixed

**1. The dismissing button was structurally undetectable** (`_dialog_bars.py`, commit 59981e3).
The run froze at turn 119 for 205 driver cycles on a PSYCH CHAPLAIN starvation notice. The bar
detector kept only the widest run per scanline, and only if it spanned 0.20 * *window* width — so
a side-by-side `Don't Show As Popup` | `OK` pair could not be represented at all, and at this
geometry (window 2560, dialog 677px, OK button 335px, floor 512px) `OK` was invisible. The
driver's rotate-on-stuck escape hatch was useless because it can only rotate through rows it is
given. Every click landed on a real button and every layer reported success.

That rotation clicked `Zoom to Base Control`, which opened a base screen *under* the modal. The
engine then crashed in that stacked state — `debug.txt`, `c0000005` at `004BF423`, a null `this`
followed by a vtable call (`8B 10 / FF 52 08`), in the unnamed drawing function below
`compute_camera`. **A driver that clicks navigation actions to unblock itself is playing the
game**, and here it played the game into an engine crash. The rule was already written on the
bead; the detector bug is what forced the driver to break it.

**2. The viability bar was never applied during a run** (`drive-unattended.py`).
`win_ladder.viability` — two bases by turn 20, the same bar for all seven factions — was written
for this exact case and only ever ran when scoring a results file *afterwards*. Seed 1 was below
that bar at turn 20 and played on for another 103 turns. The driver now applies it once, at the
checkpoint turn, and stops the run when it refuses.

## What this costs and what it saves

Spent: USD 0.54 and about four hours of wall clock, against ~USD 31 approved. The run was stopped
deliberately at turn 123 rather than played to 250 — the outcome was decided and the remaining
~4.5 hours would have measured nothing.

## The cause, and the fix

`build-stats` (thinker `be1b51b`) censused the four conditions guarding the ColonyUnit branch,
with an AI-side positive control so a row of zeros could not mean "the instrument never fired":

    player=7 reset_enter=0 reset_managed=0 build_run=0 reached=0 queued=0
             ai_reset_enter=33 ai_build_run=12

`mod_base_reset` is never called for a human faction's bases at all — `mod_bases_reset` opens
`if (is_human(faction_id)) return;` — so Thinker never chooses production for us and no colony pod
is ever queued. None of the four conditions was the cause; the governor flag the source reading
pointed at is SET (`may_pod=1`).

`na_manage_player_bases()` fixes it on the same seam as the na-ywn settle pass. Same seed, same
build: **7:1 at turn 21 before, 7:3 after**, and the viability checkpoint went REFUSED -> ok on a
live run.

The 3-seed request stays deferred until row 1 has a result.

## The gate, and what changed before the third attempt

Two defects had to close before a row could mean anything, and both did:

**na-1lj layer 1** — the site search chose destinations whose FIRST STEP cost more than a colony
pod's entire movement allowance (`path=16` valid route, `step cost=6` against `speed=3`). Movement
does not accumulate across turns, so the step was impossible and the pod sat on one tile for
nineteen turns holding a perfectly valid route. Fixed by excluding such a site and re-planning in
the same turn (thinker `32d623c`).

**na-2dg** — the engine crash at `004BF423` that ended both earlier attempts, on the dialog
reporting our HQ being lost. Fixed by malcolm (`b715cb2`): a null secondary draw-surface Release.

**The gate I set before spending another seed was expansion at rough AI parity through turn 50.**
Measured on a 55-turn run under both fixes:

| turn | 1 | 2 | 3 | 4 | 5 | 6 | **7 (ours)** |
|---|---|---|---|---|---|---|---|
| 55 | 5 | 9 | 6 | 10 | 8 | 5 | **7** |

Seven bases — mid-pack, ahead of three AI factions. Against row 1's **7:2 while the AIs held
11-31 at turn 82**, on the same seed. The gate run also reached its turn limit and exited cleanly
with no crash dump, which is the first time a seeded run has crossed the turn-120 region alive.

Seed 1 has still never produced a RESULT — refused once, crashed once — so the third attempt is
the row, not a rerun for its own sake.


## Attempt 3 — unresolved at turn 143, blocked on a Planetary Council vote

The furthest any seeded run has got, and the first to fail for a reason that is neither a stall
nor a crash.

| turn | 1 | 2 | 3 | 4 | 5 | 6 | **7 (ours)** |
|---|---|---|---|---|---|---|---|
| 55 | 5 | 9 | 6 | 10 | 8 | 5 | **7** |
| 91 | 39 | 30 | 18 | 13 | 20 | 26 | **10** |
| 143 | 53 | 47 | 43 | 0 | 47 | 66 | **12** |

**Level with the AIs at turn 55, last by turn 91.** We go linear, they go exponential. That is
the first time this ladder has measured PLAY rather than a harness fault, and it is the number
na-xb1 now has to move.

**The expansion fix worked at scale**: `colony_built=17` and **79 unaffordable-first-step
rescues** over the run, against 2 bases founded in every previous attempt. It also passed turns
119 and 126 alive — the two points where the earlier attempts died — with malcolm's `b715cb2`
holding.

**Why it stopped**: a Planetary Council vote (ELECT PLANETARY GOVERNOR). The panel needs a
CHOICE, not a dismissal — clicking VOTE opens a candidate picker with OK/CANCEL — and the harness
has no sanctioned way to make one. That is na-4lr's unfinished ROUTE half.

**Disclosed**: before ending the row I clicked VOTE twice and CANCEL once by hand trying to clear
it. None worked, and a row where the harness took game actions is not a clean measurement, so it
is recorded on the row rather than left out of it. Voting for a Planetary Governor is a game
action with consequences, which is exactly the line a win ladder must not cross to unblock
itself — so the row ends here instead.

The blocked state is kept as the fixture `council-vote-blocked`: load it and the surface is in
front of you in 60 seconds instead of 143 turns of play.
