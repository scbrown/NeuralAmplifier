# na-e55 — compound expansion arm

The original arm changed one thing from the seed-1 brain baseline: it loaded
`plan.compound-expansion.json` through `NA_PLAN`. Keep that file as the historical treatment.
The next iteration loads `plan.compound-expansion-v2.json`, with a run-local writable path through
`NA_PLAN_STATE`. The seed is experiment input and must remain byte-identical across runs.

The plan encodes the expansion **curve**, not one terminal target. Exactly one base-count
checkpoint is active at a time: 10 by turn 50, 20 by 80, 40 by 110, and 60 by 140. Each stage
asks the faction to reinvest new production capacity into the next colony-pod/former wave. A
co-equal mineral-surplus directive keeps the enabling Former/terraforming capacity in force;
the first arm otherwise selected Colony Pods while repeatedly observing zero net production.

The targets are hypotheses grounded in the committed seed-1 census, not universal SMAC laws.
Acceptance is measured by the same base census at turns 55, 91, and 140 and by
`scripts/ab_outcomes.py`; the change succeeds only if it bends the 7 -> 10 -> 12 baseline slope.
An endpoint improvement with the same linear slope is a failed arm.

Run the prepared seed-1 save with:

```sh
NA_PLAN=evals/runs/na-e55/plan.compound-expansion-v2.json \
NA_PLAN_STATE=evals/runs/na-e55/<run-id>/plan-state.json \
  <the existing na-clk seed-1 command>
```

Keep the fairness profile identical to the baseline (human slot, Talent, zero structural
handicaps) and commit the resulting census and decision log before drawing a conclusion.
