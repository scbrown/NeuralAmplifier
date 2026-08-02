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
# play a few turns, answering decisions, then copy the newest capture per surface
```

Provenance of the current set: Thinker adapter, turn 42, Morganites/Gaians,
captured 2026-08-02 while running na-1wu. `base_production_turn42.json` is the
useful one — eight legal builds with costs, roles and both turn estimates.
