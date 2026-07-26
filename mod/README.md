# mod

The thin in-game client: a GLSMAC `.gls.js` agent mod. **License: MIT.**

Loaded into GLSMAC via `--mods`. It hooks the `turn` event (and unit/base/faction events),
snapshots the visible board into a compact **world view**, sends it out through the
[`engine`](../engine/) HTTP builtin (via `Async`, so the render loop never blocks), and
applies the returned orders by calling backend bindings (`game.um`, `game.fm`, `game.tm`).

The engine stays authoritative — illegal or hallucinated orders are simply rejected. Keep
this layer **thin**: gather → call → apply. Strategy lives in the [`orchestrator`](../orchestrator/).

## Develop

```bash
just mod lint    # prettier --check
just mod fmt     # prettier --write
just mod test    # load the mod against a stub server; assert payloads + applied orders
```

Testing points the HTTP builtin at a local stub server that records the outgoing world view
and replays canned orders — no Claude required. See
[../docs/building-and-testing.md](../docs/building-and-testing.md).

> Scaffolded in roadmap Phase 0 (see [../VISION.md](../VISION.md)).
