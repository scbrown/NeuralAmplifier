# Captured world views

Real `WorldView` payloads harvested from a running game by `WorldViewStore`
(`NA_WORLD_VIEW_STORE`), one per surface, kept as the richest capture available
for that surface (na-oh5).

**These are not golden files.** Nothing compares against a stored expectation, so
a newer capture can replace an older one outright. They exist for one reason:
every other fixture in this suite is hand-written, and a fixture written to match
the contract cannot catch an adapter that does not match it. That is exactly how
`history` came to ship on both sides, wired to nothing, while passing every test
(na-wzw).

Refresh:

```bash
NA_BRAIN=agent NA_WORLD_VIEW_STORE=/tmp/wv \
  uv run --directory orchestrator neural-amplifier serve
# ... play a few turns, then:
scripts/harvest-world-views.py /tmp/wv            # what would change
scripts/harvest-world-views.py /tmp/wv --write    # do it
```

**Use the script, not a manual copy.** The manual step is how `faction.tech` and `base.hurry`
were lost twice: it is a judgement call made at the end of a long session, about surfaces the
session was not working on. The script copies EVERY surface present and reports the ones it did
not see, so a rare capture cannot be dropped by being forgotten (na-ibh).

It never prunes a fixture whose surface is absent from the store — a run that did not reach
`base.hurry` says nothing about the `base.hurry` capture you already have.

Provenance: Thinker adapter.

- `base_production_turn42.json`, `faction_se_turn42.json` — turn 42,
  Morganites/Gaians, captured 2026-08-02 running na-1wu. `base_production` is the
  useful one — eight legal builds with costs, roles and both turn estimates.
- `faction_tech_turn135.json` — turn 135, Hive, captured 2026-08-03 running the
  na-1g7 live exercise. A far more developed game (49 bases), so its faction-scope
  metrics are non-trivial rather than early-game zeros.

**faction.tech had no capture at all until 2026-08-03**, despite being probed live
during na-b4v and na-1wu: it was harvested to a `/tmp` store and never committed,
and `/tmp` was cleared. That is what na-ibh exists to stop — copy the newest capture
for EVERY surface that fired, not just the one the current task needed.

- `base_hurry_turn42.json` — turn 42, Gaians, Gaia's Landing hurrying a Colony Pod.
  Recovered 2026-08-03 (na-qu8). Carries `subjects: ["Colony Pod"]`, the two-option
  action space, and `native_choice: "hurry:none"` — the deterministic tier's answer,
  which is what an eval on this surface compares against.

**Two things this one corrects, and both are about where you look.**

`base.hurry` was recorded as uncaptured, on the reasoning that it fires only when a base
can actually be hurried *and the engine asks*. That is true of the ORGANIC path and it is
not the only path: the adapter has a **serialiser-only probe**, `observe-hurry <base_id>`
(`thinker/src/neural.cpp:4127`), which emits the full surface for any base on demand,
reading live numbers with `native_hurried = 0` so it never spends credits. A surface that
is hard to catch organically is not therefore hard to capture.

And it was not lost. It was in the play directory's `na-observations.jsonl` the whole time
— the adapter's own log, which lives in the SMAC install and not in this repo. Two separate
sweeps concluded "no base.hurry capture survives anywhere on disk" after searching the repo
and `/tmp`, which is where a world-view STORE lives; the adapter log is a different sink in
a different tree. **When harvesting, check both: the `NA_WORLD_VIEW_STORE` directory AND
`$PLAY_DIR/na-observations.jsonl`.**

`harvest-world-views.py` now does, so this is no longer a thing to remember (na-0oa). It takes
`--log <file-or-play-directory>`, repeatably, and reads `SMAC_PLAY_DIR` automatically; the store
argument is optional, because a session that only has the adapter log is exactly the case it was
blind to. Each row of the report names the sink its capture came from, so "the store had it" and
"only the log had it" stay distinguishable. Log lines that are not world views — the compact
divergence records `na_verify_*` emits carry a `surface_id` and nothing else the contract
requires — are skipped rather than harvested as fixtures.
