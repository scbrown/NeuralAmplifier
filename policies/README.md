# Board policies

Graph-pattern policies for the yupana board guard (`orchestrator/src/neural_amplifier/yupana.py`).
Point the orchestrator at a file of them:

```bash
export NA_YUPANA_URL=http://127.0.0.1:3040
export NA_YUPANA_POLICIES=policies/board.example.json
just play
```

**The JSON file is the fallback; Quipu is the source.** Policies are governance — a rule wants
provenance, a history and one owner, not a file somebody edited. `board.ttl` is the authored
form, as `aegis:Policy` nodes, and the orchestrator projects it:

```bash
quipu knot policies/board.ttl --db .quipu/policy.db
quipu-server --db .quipu/policy.db --bind 127.0.0.1:3031
export NA_POLICY_QUIPU_URL=http://127.0.0.1:3031
```

With that set, `NA_YUPANA_POLICIES` is only consulted when the store cannot be read — and the
fallback is logged, because guarding with a stale file while saying nothing is how a run ends up
enforcing rules nobody can point at. It is a *fallback*, never a merge: two live sources of
governance is the drift this was meant to remove.

The field names in `board.ttl` are quipu's own governance vocabulary
(`shapes/aegis-properties.ttl`), and yupana's `StatePolicy` deserialises exactly that shape in
snake_case — its `Boundary::as_str` is documented as "the wire spelling of `aegis:boundary`". So
the projection is a rename, not a translation between two ontologies, and there is no second
vocabulary here to drift. Only `boundary "order"` policies are handed to the board guard; quipu's
graph holds the whole agent's constraints, and one written for the pre-edit seam would otherwise
come back `unevaluated` on every decision.

## Turning the board on

The base policies need `WorldView.bases`, which the Thinker adapter publishes only when asked:

```ini
; thinker.ini
na_board_state=1
```

Off by default there for a real reason — the array rides on every world view and the world view
is the prompt, so a faction with thirty bases pays for thirty bases on a decision about one of
them. With it off, the base policies report `vacuous` rather than passing silently.

## What is enforceable today, and what only warns

`reserves-stay-solvent` **denies**, and is verified end to end: reserves 40, a hurry costing 81,
the guard strips the order, the brain is re-asked with the claim, and it takes the legal
alternative — one decision, `repairs: 1`, not a lost turn.

`garrison-exposed-bases` **can only warn today**, and it is worth understanding why before you
rely on it. `Action.effects` is keyed on the *metrics* vocabulary and lands on the faction node,
so "disbanding this unit empties that base's garrison" is not expressible as an order effect.
Yupana's overlay therefore sees the board unchanged, the policy fires against the pre-order state,
and yupana marks it `pre_existing` — which correctly warns rather than strips, because the order
did not cause the condition:

```text
A base near a hostile faction must keep at least one garrison unit
  — already true before these orders (?b=base:1, ?d=8)
```

That is real and useful: the brain is told an exposed base is empty. It is not enforcement, and
calling it enforcement would be the exact kind of claim this project walks back. na-n72 tracks
the contract extension, and is deliberately not built yet — every surface whose orders would move
a garrison is unit-scope and in `NO_AI_PATH`, so the brain cannot decide one today.

## Reading `defend_range`

The one field a policy is most likely to get wrong. It is **not a distance in tiles**. Thinker
computes it once per faction turn as a weighted enemy-proximity figure — `map_range` × 2 for a
same-region enemy or × 3 otherwise, × 1 at war or × 4 at peace, halved, capped at 50 — so **lower
means more exposed**, and the same neighbour scores 8 at war and 32 at peace. Thinker's own build
code treats `< 12` as "wants perimeter defence", which is the nearest thing to a house definition
of a border base, and is where the starter policy's threshold comes from.

It also refreshes once per faction turn while world views are emitted at decision time, so within
a turn it can lag a few unit moves.

The threshold lives here, in the policy, and not in the adapter. That is deliberate: a threshold
compiled into the DLL is one nobody can change without a cross-compiler, and "is this a border
base" is a judgement, while `defend_range` is a fact.

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
