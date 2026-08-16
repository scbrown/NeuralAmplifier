# Board policies

Graph-pattern policies for the yupana board guard (`orchestrator/src/neural_amplifier/yupana.py`).
Point the orchestrator at a file of them:

```bash
export NA_YUPANA_URL=http://127.0.0.1:3040
export NA_YUPANA_POLICIES=policies/board.example.json
just play
```

**These are a starter, not the source of truth.** Policies are governance: they are authored in
Quipu and projected, and a copy compiled into this repository would enforce yesterday's rules
while looking current. The file is read per process so a run states which policy set it played
under.

## What is live today, and what is inert

`reserves-stay-solvent` **works now**, and is the one verified end to end: reserves 40, a hurry
costing 81, and the guard denies the order, tells the brain the claim, and the brain picks the
legal alternative — one decision, not a lost turn.

`garrison-border-bases` and `hold-expansion-under-threat` are **inert today**, and deliberately
shipped anyway. They name board attributes — `smac:isBorderBase`, `smac:garrisonCount`,
`smac:underThreat`, `smac:expanding` — that the Thinker adapter does not yet emit into
`WorldView.bases`. Their selectors therefore match nothing.

That is not a silent failure, and it is the reason they are worth having in the file now. Yupana
reports a selector that matched nothing under `vacuous`, and the guard forwards it as an advisory
on every decision:

```text
policy matched nothing: garrison-border-bases — selector `?b a smac:BaseState ;
smac:isBorderBase true` matched no node on the post-order board — the policy was never asked,
which is not the same as satisfied
```

So the gap is stated on the record every turn rather than passing as a clean board. When the
adapter starts publishing those fields the policies begin firing with no change here — and if
someone renames an attribute on either side, the same `vacuous` line is what says so.

## Writing one

The selector language is a small ASK-style subset over the board graph — no inference, no
property paths, no `OPTIONAL`, no `UNION`, no negation. A predicate that starts wanting those is
the signal the policy belongs in Quipu, not the signal to grow the pattern language.

```text
pattern := clause { '.' clause } [ '|' filter { ',' filter } ]
clause  := ?var pair { ';' pair }
pair    := ('a' | name) term
filter  := ?var ('=' | '!=' | '<' | '<=' | '>' | '>=') literal
```

Three things to get right:

- **Names are matched byte for byte.** Yupana does no prefix expansion, so `smac:garrisonCount`
  matches an attribute literally called `smac:garrisonCount`. Everything the guard ingests is
  prefixed `smac:` — write the same spelling or the selector matches nothing.
- **`boundary` must be `order`** and **`selector_lang` must be `graph-pattern`**. `sparql` is
  reserved for policies Quipu evaluates and is refused here rather than approximated; the guard
  reports it under `unevaluated`.
- **`effect: deny` strips the order, `warn` only annotates.** A finding yupana marks
  `pre_existing` — the condition already held before these orders — never strips whatever its
  effect, because denying a move for something it did not cause removes a legal and possibly
  correct option.
