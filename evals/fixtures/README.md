# Save-state fixtures

A fixture is a `.sav` plus a `.json` manifest: a specific game state, addressable by name, that
any run can start from.

    scripts/fixture.py capture <name> --play-dir DIR --note "why this state matters"
    scripts/fixture.py list
    scripts/fixture.py show <name>

    NA_SAVE=evals/fixtures/<name>.sav scripts/play-thinker.sh headless

## Why

Every na-1lj hypothesis cost a fresh 15-minute run: launch, play to turn 15-25, wait for a colony
pod to exist AND freeze, then measure. Nine hypotheses, nine runs — and each landed on a
different map position with different neighbours, so no two runs were the same experiment. That
confound is why `settle-mode` and `unit-turn` had to be built as in-game switches to be
measurable at all.

**Measured: loading `frozen-pod-seed1` reproduces the state in 59 seconds**, byte-identical, with
the pod on the same tile holding the same waypoint. Same experiment every time, about fifteen
times faster.

na-6db already proved the shape — two arms from one byte-identical save, md5 verified. This makes
it the method rather than something done once by hand.

## The manifest is the point

A save with no record of why it was kept is a file nobody dares delete and nobody can use. Each
manifest carries the turn, the md5, and the `game-state` / `move-stats` / `build-stats` readings
taken at capture — so "the pod was stuck" is a reading (`pod=136,84 wp=125,83 order=24 spent=3
speed=3 zoc=0 fung_dir=0`) rather than a memory.

`fixture.py show` re-hashes the `.sav` and says MISMATCH if the bytes have changed. A fixture
whose bytes moved is not the fixture any published result cited.

## Why `NA_SAVE` and not `NA_RESUME`

`NA_RESUME` picks the newest autosave by mtime. That is right for continuing a run and wrong for
an experiment: it makes the thing under test depend on whatever else wrote to the directory.
`NA_SAVE` addresses one file by name and is mutually exclusive with `NA_SEED` — a run cannot both
load a state and generate a fresh map.
