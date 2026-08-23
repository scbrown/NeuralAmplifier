# na-6db — the first brain-vs-native outcome measurement (M1)

Two arms played forward from a byte-identical save, LLM pinned to the human slot per na-xb1.

```console
$ python3 scripts/ab_outcomes.py --faction 7 \
    evals/runs/na-6db/baseline.faction7.jsonl \
    evals/runs/na-6db/brain.faction7.jsonl

faction 7 (Peacekeepers)
turns 101–140 (40 shared)
fairness: slot=human difficulty=transcend structural handicaps=0

metric                 baseline      brain      delta
base_count                   18         16         -2
pop_total                    81         74         -7
mineral_surplus               3          3         +0
energy_reserves            2579        226      -2353
energy_income                53         48         -5
labs_output                  32         30         -2
military_units               43         48         +5
drone_total                  33         28         -5
```

## The arms

|  | baseline | brain |
|---|---|---|
| `llm_factions` | `0` | `128` (bit 7 = Peacekeepers, the human slot) |
| save | `saves/auto/Autosave_2200.sav` | the same file, md5 `3d654478…` verified identical |
| `manage_player_bases` / `manage_player_units` | 1 / 1 | 1 / 1 |
| difficulty | transcend | transcend |
| brain | — | `claude-haiku-4-5`, 701 decisions, 0 degraded, $2.61 |

Both ran headless under `scripts/play-thinker.sh` with `scripts/drive-unattended.py` clearing
in-play dialogs, and both exited at `NA_EXIT_TURN=140`.

## What is in these files

The **faction-7 metric-bearing records only**, sliced from each arm's `na-observations.jsonl`.
The full logs are ~34,000 and ~15,000 records covering all seven factions; the slice is what the
comparison reads and what makes the result re-derivable from the repo rather than re-trustable
from a bead. Re-running the command above reproduces the table exactly.

`--faction 7` is required, not decoration: one observation log holds every faction, and without
it the comparison splices seven trajectories together (see `scripts/ab_outcomes.py`).

## What it measures, and what it does not

The brain arm is **base.hurry** — 699 of 701 decisions, plus 2 `faction.tech`.
`base.production` fired **zero** times for the human slot across all 40 turns, because
`mod_base_build` sits on an event path (`mod_base_reset`) rather than the per-turn one. So this
is a measurement of the hurry policy, not of "the brain playing the faction".

54 rush-buys out of 699 (7.7%) took the treasury from 632 to ~130 by turn 111 and held it near
200 for the rest of the run while the baseline compounded to 2,579. `energy_income −5` is the
consequence that matters: by turn 140 the brain's economy is structurally smaller, not merely
poorer.

One save, one seed. The tool prints no verdict for that reason; na-clk is the instrument that
turns a trajectory into a claim.
