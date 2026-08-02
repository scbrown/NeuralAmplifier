# na-s4e — the positive control the fix shipped without

`0722b7b` changed `metrics.energy_income` from gross base energy to the engine's own net
expression. It shipped **source-verified and no-regression-verified**, with its own limit stated
plainly: the A/B did not diverge at the turn-35 save, because commerce and maintenance cancelled
to zero there, so **no state with `facility_maint_total > 0` had ever been observed to differ**.

This is that observation. It confirms the fix and it **corrects the bead's headline**.

## Method

Two builds of `thinker.dll` replaying one identical save — no brain, no model, no API spend.
The engine replays deterministically, so the two runs walk the same states.

| arm | thinker | expression | dll sha1 |
| --- | --- | --- | --- |
| `gross` | 2929637 | `energy_surplus_total` | `f31625a3` |
| `net` | 0722b7b | `energy_surplus_total + turn_commerce_income - facility_maint_total` | `e2dbc9e5` |

Save `saves/auto/Autosave_2234.sav` (sha1 `bf33f458…`), turn 135, three factions at 49–55 bases
— chosen because facility maintenance at that size cannot be zero. The save was restored from a
pinned copy and re-verified by sha before the second arm, so both arms loaded the same bytes.

Rows are paired on a **strict state fingerprint** — surface, faction, base id, base name, turn,
`call_seq`, and four independent aggregates (`energy_reserves`, `base_count`, `pop_total`,
`military_units`, `labs_output`). Only pairs agreeing on all of it are compared: **69 of ~126**.
Pairing on `energy_reserves` alone was tried first and rejected — one reserve value recurs across
different moments, and it produced a table that was wrong in both directions.

Because `old - new == facility_maint_total - turn_commerce_income` by construction, any pair with
`old > new` **proves `facility_maint_total > 0`** without needing the component fields, which the
adapter does not emit.

## Result — the control is achieved

**56 of 69 matched pairs have `old > new`.** Maintenance is provably nonzero, and the largest
single proven value is **`facility_maint_total >= 70`** (University, New Arzamas).

Collapsing repeated rows to distinct value pairs — 49 of the 56 are the same Hive figure, so row
counts overstate the evidence and distinct pairs are the honest unit:

| faction | gross → net | proves | old overstates the setback denominator by |
| --- | --- | --- | --- |
| University | 148 → 78 | maint ≥ 70 | 90% |
| University | 136 → 76 | maint ≥ 60 | 79% |
| University | 116 → 67 | maint ≥ 49 | 73% |
| University | 40 → 17 | maint ≥ 23 | 135% |
| University | 26 → 9 | maint ≥ 17 | 189% |
| Hive | 26 → 6 | maint ≥ 20 | 333% |
| University | 10 → 7 | maint ≥ 3 | 43% |
| Gaians | 213 → 208 | maint ≥ 5 | 2% |

`setback_turns` divides by this number, so a denominator overstated by 333% understates the
recovery time by about three quarters. That is the harm the bead described, now measured.

## The correction: "systematically optimistic" is too strong

The bead's title says `setback_turns is systematically optimistic`. Measurement does not support
the word *systematically*, and the exception is **our own faction**.

For Gaians, **11 of 12 distinct pairs run the other way** — the old gross value was too *low*,
by as much as 74 (473 → 547):

| faction | direction | distinct pairs |
| --- | --- | --- |
| Gaians | old too LOW (commerce > maintenance) | 11 of 12 |
| Hive | old too HIGH | 1 of 2 (other cancels at 0 → 0) |
| University | old too HIGH | 6 of 7 |

The sign depends on whether **commerce or maintenance dominates**, and the old value excluded
*both*. So the honest statement is: the gross value is **systematically wrong, in a direction
that varies by faction** — optimistic where maintenance dominates, pessimistic where commerce
does. Gaians is commerce-heavy, so on the faction we actually play the old number erred toward
*over*stating the setback.

**This does not weaken the fix, and it is not a refutation.** `RATE_OF` maps `energy_reserves` to
`energy_income`, reserves move by net, and net is what the new expression computes. The
justification is unchanged; only the characterisation of the damage was wrong, in the same way
na-373's ranking argument was wrong — a plausible mechanism stated with more confidence than the
measurement behind it.

## An unrelated thing this surfaced, not caused by the fix

On `base.production`, Hive and University report `energy_income: 0` **and** `labs_output: 0`
while `energy_reserves` (707, 547) and `base_count` (49, 55) are populated — and **both arms
agree**, so it predates 0722b7b. A 49-base faction does not have zero gross energy or zero labs
output; those aggregates are evidently not refreshed at that observation point for a faction
whose turn is not being processed.

It matters because a faction-scope directive evaluated on such a world view divides by a
denominator that is zero for a reason nobody can see from the record: **"not computed yet" and
"genuinely zero" are the same two characters.** Not filed against this bead — it is a separate
defect and belongs in its own.

## Limits, stated

- One save, one turn, three factions. Deep, but not a sweep across game stages.
- `facility_maint_total` is proven nonzero by the *difference*; the component itself is still not
  emitted, so the values above are lower bounds (`maint >= old - new`, since commerce >= 0).
- Both arms ended on the harness timeout rather than a turn limit. That bounds how many turns
  were walked; it does not affect a within-state comparison, which is what this is.
