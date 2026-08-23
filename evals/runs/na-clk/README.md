# na-clk seed 1 — the ladder's first seeded full game

**Result: refused as unplayable at turn 123. The seed measured a faction that could not
expand, not the quality of its decisions.**

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

**2. The viability bar was never applied during a run** (`drive-unattended.py`, this commit).
`win_ladder.viability` — two bases by turn 20, the same bar for all seven factions — was written
for this exact case and only ever ran when scoring a results file *afterwards*. Seed 1 was below
that bar at turn 20 and played on for another 103 turns. The driver now applies it once, at the
checkpoint turn, and stops the run when it refuses.

## What this costs and what it saves

Spent: USD 0.54 and about four hours of wall clock, against ~USD 31 approved. The run was stopped
deliberately at turn 123 rather than played to 250 — the outcome was decided and the remaining
~4.5 hours would have measured nothing.

The 3-seed request should stay deferred. Drawing more seeds now buys three more refusals.
