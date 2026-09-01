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

## v2 bounded checkpoint result

`v2-turn99-result.json` records a bounded turn 99–111 run from the preserved clean save. The
capacity directive changed every production decision: 58 chose Formers (`unit:1`) and 14 chose
Recycling Tanks (`facility:3`), with zero Colony Pods; all 72 production decisions reported that
they followed `terraform-capacity-before-pods`. The transport was clean (145 decisions, zero
degraded) and cost $10.9909.

The turn-110 census was still **6 bases**, identical to the original compound arm's turn-110
checkpoint. A continuation from the preserved turn-111 save through turn 140 stayed at **6 bases
for every saved turn**, versus 7 for v1 at turn 140. Across the complete v2 arm, 246 production
decisions chose 116 Formers, 119 Recycling Tanks, 11 Children's Creches, and zero Colony Pods;
493 total decisions were clean, with zero degraded calls, at $41.351788. This falsifies both the
immediate and delayed versions of the capacity-enabler hypothesis. The next discriminating arm
must test an explicit handoff from accumulated Former capacity to Colony Pods rather than leave
the enabler co-equal through the endpoint.
