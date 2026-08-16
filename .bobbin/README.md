# Dev-time guardrail (Hank role b / K5)

`config.toml` here holds structural rules that `yupana hook pre-edit` evaluates against the text
an edit *introduces* — this repository's own invariants, checked while the edit is still a
proposal. Wired in `.claude/settings.json` as a `PreToolUse` hook on `Edit|Write`.

## It cannot break your session

The hook is `command -v yupana >/dev/null 2>&1 && yupana hook pre-edit || true`, so with no
yupana on PATH it is silent and exits 0 — which is the common case, and deliberately the
uninteresting one. Yupana itself then fails open on top of that: an unparseable config, a blown
deadline, a rule that does not compile all let the edit through. A guard that can block work has
to be less likely to fail than the work is.

`mode = "advise"` rather than `enforce`. These rules catch a *shape*, not an intent, and none of
them can tell a violation from a deliberate exception. The author is told and decides; a guard
that denies on a heuristic is one people turn off.

## Building yupana so the rules actually run

**This matters more than it looks.** Python support is behind a Cargo feature, and without it
yupana parses no `.py` file, finds no applicable rule, and says nothing — because "this build
cannot parse the language" is correctly *not* a gap worth reporting in a Rust repo. In this
repository it means every rule below is inert while the hook looks healthy:

```bash
cargo build --release --features "mcp,game-state,langs-extra" --bin yupana
```

`langs-extra` is the one that matters here. If you are seeing no advisories ever, check that
first — it is the failure this whole file is most likely to hit.

## The rules

| Rule | What it catches |
| --- | --- |
| `degradations-are-recorded-not-swallowed` | `except …: pass` in the knowledge, board-guard or memory seams. Those layers are required to degrade *visibly*; a swallowed failure is indistinguishable from a clean run. |
| `no-engine-specifics-in-the-orchestrator` | `engine == "thinker"` in `orchestrator.py`. Invariant 2: the orchestrator speaks only the contract and must never learn which engine it is driving. |
| `degraded-is-not-deterministic` | Code equating the two. `policy.py` explains at length why they must stay apart — collapsing them puts a deliberate configuration into `degrade_rate`, the one number that catches a silently absent brain. |

Each was verified to fire on a violating edit and stay silent on a clean one.

## Not the whole of role (b)

The architecture wants these projected from Quipu's canonical `aegis:Policy` rows rather than
authored in a file, exactly as the board policies now are (`policies/`). The field names here
mirror those atoms 1:1 — `query` is `aegis:Selector.evidenceSource`, `pattern` is
`aegis:Predicate.evidenceSource`, `match_type` is `aegis:matchType` — so that move is a
projection rather than a rewrite. Yupana already reads a projected rule set; what is missing is
the authored source and the sync, which is the same shape `policies/board.ttl` took.
