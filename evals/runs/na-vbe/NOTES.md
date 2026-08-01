# na-vbe — compacting grounding against the action space

**Question.** na-373 refuted rank-then-truncate and left the criterion behind: retrieval may get
cheaper only if the *choice distribution* does not move. Compaction is the other way to get
smaller — keep every option's fact, shorten all of them the same way. Does evenness suffice?

**What was dropped.** `describe(compact=True)` removes two parts from every fact alike:

- **cost** — the adapter ships a faction- and difficulty-adjusted *mineral* cost
  (`na_write_action_space` multiplies rulebook "rows" by `mod_cost_factor`) plus
  `turns_if_switched`. The graph holds the raw rows. Emitting both put `cost 8` beside `cost 80`
  about the same item. `quipu.format_row` had excluded cost all along for this reason and
  `briefing.describe` had not — the two retrievers disagreed, and this closes that.
- **prerequisite** — the option is *in* the action space, so the engine already ruled it
  buildable and the tech gate is satisfied by construction.

## Result

| arm | n | prompt chars | offered | utilisation | choices |
| --- | --- | --- | --- | --- | --- |
| verbose | 20 | 9996 | 8 | 0.16 | `build:3`×20 |
| compact | 20 | 9732 | 8 | 0.17 | `build:3`×19, `build:1`×1 |

Grounding itself went 607 → 343 chars, **43% smaller**. The choice distribution held: Fisher
exact two-sided **p = 1.000**. Utilisation is flat, which is the honest reading — compaction
removes offered *text*, not offered *facts*, so the denominator never moved and there was no
mechanism for it to rise. It is reported because it is what the change was nominally aimed at,
and it did not deliver that; the cost reduction is the whole of the win.

**Passes the na-373 criterion.** Cheaper retrieval, same decision.

## The first run was measuring a bug

An earlier pass scored compaction at `build:3` 13/20 against an 18/20 baseline (Fisher p=0.13)
and looked like a mild failure. It was not measuring the change.

`Action` declares only `id`/`action`/`effects`, but `contract._Model` sets `extra="allow"` and
the brain sends `world_view.model_dump_json()` — so every field the adapter writes reaches the
prompt whether or not the model names it. The fixture was built from the declared fields alone,
so its `action_space` carried **no cost at all**. Grounding was therefore the only thing in that
prompt with a price in it, and removing it removed the discriminator between the two contenders
rather than removing a duplicate. `Research Hospital` has the strictly better effect and is 1.5×
the price; with no price visible it won more often, exactly as it should have.

`harness._adapter_action` now shapes actions the way the adapter shapes them. Both arms above
were re-run on the fixed fixture; neither number from the first pass survives.

The general lesson is not about cost. A fixture assembled from a contract's *declared* fields is
not the payload when the contract tolerates extras — and the gap is invisible, because both
sides parse and both sides validate. Anything measuring prompt content has to be built from what
the writer emits, not from what the reader names.

## Reproducing

```bash
just eval score na-vbe     # committed answers, no game, no model, no sibling repo
just eval check na-vbe     # do those answers still belong to the current prompt?
```
