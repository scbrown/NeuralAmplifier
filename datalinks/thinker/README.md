# Thinker-sourced datalinks graph

Generated from [`scbrown/thinker`](https://github.com/scbrown/thinker)'s
`docs/alphax.txt` by `just ingest-thinker`. Regenerate rather than hand-edit.

## This is tagged `house-rule`, not `canonical`

Thinker ships **its own** `alphax.txt` — the header reads *"SMACX Thinker Mod /
Default tech tree for the expansion"*. It is byte-for-byte the same *shape* as
stock SMAC's, which is exactly why it is dangerous: once stored, nothing
downstream can tell a mod's tech tree from Firaxis's.

So every node here carries:

```turtle
smac:appliesToEngine "thinker" ;
smac:ruleTier        "house-rule" ;
```

Retrieval filters on `appliesToEngine ∈ {smac, <current engine>}`, so these
facts surface in a Thinker game and never masquerade as canonical SMAC in a
GLSMAC one. `neural-amplifier ingest` **refuses** to tag a file whose header
announces a mod as `canonical`.

## The canonical graph is not here

Stock rules come from your own SMAC install and are derived copyrighted game
data, so they are gitignored (`/datalinks/*`). Produce them with:

```bash
SMAC_DIR=/path/to/smac just ingest    # → datalinks/smac.ttl, tagged canonical
```

Diffing the two is the point: it shows exactly where Thinker deviates from
stock, which is the fairness-ledger question asked of the rules rather than of
the binary.
