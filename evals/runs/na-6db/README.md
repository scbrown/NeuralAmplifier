# na-6db — the first brain-vs-native outcome measurement (M1), and its first fix (M2)

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


---

# M2: the same brain with one standing directive

`brain-directive.faction7.jsonl` is a third arm, identical to the brain arm except for
`plan.hold-reserve-floor.json` loaded via `NA_PLAN`:

```json
{"id": "hold-reserve-floor", "metric": "energy_reserves",
 "comparator": "at_least", "target": 600, "priority": 8}
```

```console
$ python3 scripts/ab_outcomes.py --faction 7 \
    evals/runs/na-6db/baseline.faction7.jsonl \
    evals/runs/na-6db/brain-directive.faction7.jsonl

metric                 baseline      brain      delta
base_count                   18         18         +0
pop_total                    81         80         -1
mineral_surplus               3          0         -3
energy_reserves            2579       1237      -1342
energy_income                53         53         +0
labs_output                  32         34         +2
military_units               43         55        +12
drone_total                  33         27         -6
```

All deltas against the deterministic baseline, M1 arm vs M2 arm:

| metric | brain | brain + directive |
|---|---|---|
| base_count | −2 | **+0** |
| energy_income | −5 | **+0** |
| pop_total | −7 | **−1** |
| labs_output | −2 | **+2** |
| military_units | +5 | **+12** |
| drone_total | −5 | **−6** |
| energy_reserves | −2353 | **−1342** |

`energy_income` returning to parity is the load-bearing one: M1's finding was that the brain had
bought a permanently smaller economy, and that is gone.

723 decisions, 1 degraded, $2.95. The directive was `in_force` on all 723, `not_shown` on none,
declared **followed on 661 (91%)**, **overridden on 0**, and **`unsatisfied` on 0** — the floor
was never breached in 40 turns.

It did not hurry less (51 rush-buys against the undirected arm's 54). It bought cheap completions
that clear the floor instead of expensive rushes that breach it, and said so in its reasoning.

**This is not a win.** Parity-or-ahead on five of eight trajectory metrics on one save is not a
victory condition; na-clk is the instrument for that. Override rate 0 also means this says
nothing about a directive that costs something.
