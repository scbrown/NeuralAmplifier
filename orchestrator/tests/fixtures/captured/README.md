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

`base.hurry` is still uncaptured. It fires only when a base can actually be hurried
and the engine asks, which did not happen in the na-1g7 run. na-qu8 stays blocked on
it.
