# na-mmp — real-game directive attention

`decisions.jsonl` is the retained decision log from the M2 `m1c` arm, turns
101–140. It is the source for na-mmp's per-directive measurement; the separate
`na-6db` faction logs retain the trajectory outcome comparison.

Re-derive the report without a game install, model key, Wine prefix, or sibling
checkout:

```console
$ scripts/directive_report.py evals/runs/na-mmp/decisions.jsonl
723 decisions with a plan block

directive              pri  in force   attn  override  unmeas
-------------------------------------------------------------
hold-reserve-floor       ?       723   0.91      0.00       0
```

The source records carry directive IDs and outcomes but not the plan definition,
so priority renders as `?`; `evals/runs/na-6db/plan.hold-reserve-floor.json`
retains the priority-8 definition. The report's measured counts remain complete:
in force 723, followed 661, overrode 0, unmeasurable 0, unsatisfied 0.
