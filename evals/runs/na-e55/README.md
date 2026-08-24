# na-e55 — compound expansion arm

This arm changes one thing from the seed-1 brain baseline: load
`plan.compound-expansion.json` through `NA_PLAN`.

The plan encodes the expansion **curve**, not one terminal target. Exactly one base-count
checkpoint is active at a time: 10 by turn 50, 20 by 80, 40 by 110, and 60 by 140. Each stage
asks the faction to reinvest new production capacity into the next colony-pod/former wave.

The targets are hypotheses grounded in the committed seed-1 census, not universal SMAC laws.
Acceptance is measured by the same base census at turns 55, 91, and 140 and by
`scripts/ab_outcomes.py`; the change succeeds only if it bends the 7 -> 10 -> 12 baseline slope.
An endpoint improvement with the same linear slope is a failed arm.

Run the prepared seed-1 save with:

```sh
NA_PLAN=evals/runs/na-e55/plan.compound-expansion.json <the existing na-clk seed-1 command>
```

Keep the fairness profile identical to the baseline (human slot, Talent, zero structural
handicaps) and commit the resulting census and decision log before drawing a conclusion.
